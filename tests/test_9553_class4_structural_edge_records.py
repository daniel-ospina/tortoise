"""R5 class 4 (#9553): the structural-edge lane JOURNALS the edges it writes.

THE DEFECT THIS PINS. The lane's two mints (`create_edge` + its three typed
wrappers, `create_about_edge`) issue one bare `MERGE` each and emitted no
record. Measured on `main` before this change (`/tmp`-free reproduction is
`test_the_measured_gap_before_this_change` below): `ownedBy`, `dependsOn`,
`hasPart`, `wasDerivedFrom` and `references` were written to the graph,
`rebuild_all` reported **`edges: 0`** for every one of them, and the record's
`relation` table never saw them — so a record-sourced rebuild reproduced the
CLAIMS and lost the TOPOLOGY, which is exactly what R5 exists to prevent.

WHAT IS ASSERTED, AND WHY EACH LEG DISCRIMINATES.

* the mint journals §4.4's flat `DirectEdgeCreated` descriptor — the SAME shape
  `create_direct_edge` emits, so pass-2b's existing fold consumes it and
  `record_projection`'s existing `RelationUpsert` materialises it. No new record
  type, no second edge store, no `relation` column;
* BOTH mints, and the three typed wrappers that route through `create_edge` —
  the producer sits at the projection's two statements, so a door added later is
  covered without being named;
* the descriptor carries `source_label`/`target_label`, which the JOURNAL fold
  ignores and the RECORD needs (`relation.src_label`/`tgt_label` are bound
  values, and each label resolves through its own key column);
* the endpoints are the ones the record can ADDRESS — the fold's own probe
  (`EPISTEMIC_LABELS`, `p.id`). A `:Source` endpoint is declined and disclosed
  once, never half-journaled (a descriptor the journal rebuild cannot consume
  would make the record RICHER than the graph — the one direction Interface A
  forbids);
* an unbound projection (a test fold, `rebuild_all`, `FalkorProjection.from_uri`)
  emits nothing: the recorder is the SDK's, so no projection invents a journal.
"""
from __future__ import annotations

import json

import pytest

from tortoise import record_projection
from tortoise.live import EPISTEMIC_LABELS
from tortoise.projection.edges import (
    _VALID_ABOUT_EDGE_PREDICATES,
    _VALID_EDGE_PREDICATES,
    RECORD_ADDRESSABLE_EDGE_LABELS,
    RECORD_CARRIED_STRUCTURAL_RELS,
    SOURCE_TARGETED_ABOUT_RELS,
    STRUCTURAL_EDGE_LANE_PREDICATES,
    SUPERSEDE_TRANSFERRED_RELS,
    structural_edge_record_carrier,
)
from tortoise.sdk import TortoiseSDK

#: The predicates `create_about_edge` accepts (`_VALID_ABOUT_EDGE_PREDICATES`),
#: with the target each one's ONTOLOGY §3.2 triple requires — restated so the
#: behavioural leg writes a LEGAL edge per predicate instead of letting the mint
#: pick a label. ⚠️ `aboutAction` is NOT here: the about MINT does not accept it
#: (it is a `create_edge` predicate whose target is a Point), so it rides the
#: generic leg below.
ABOUT_RELS = frozenset({
    "aboutSubject", "aboutObject", "aboutEvent", "aboutPoint",
})
ABOUT_TARGETS = {
    "aboutSubject": "subject",
    "aboutObject": "object",
    "aboutEvent": "event",
    "aboutPoint": "p2",
}


def _records(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _direct_edges(path) -> list[dict]:
    return [r for r in _records(path) if r.get("type") == "DirectEdgeCreated"]


@pytest.fixture(autouse=True)
def _reset_gap_warnings():
    """`_RECORD_GAP_WARNED` is once-per-relation per PROCESS — clear it here.

    Without this the disclosure legs would depend on test ORDER: the first file
    that declines a `references` write silences the next one's log assertion,
    and the suite would pass or fail on collection order rather than on the
    behaviour.
    """
    from tortoise.projection import edges as _edges

    _edges._RECORD_GAP_WARNED.clear()
    try:
        yield
    finally:
        _edges._RECORD_GAP_WARNED.clear()


@pytest.fixture()
def edge_sdk(tmp_path):
    """A real SDK over an embedded graph, with a JSONL journal (so it emits)."""
    journal = tmp_path / "journal.jsonl"
    sdk = TortoiseSDK(str(tmp_path / "edges.db"), event_log_path=str(journal))
    try:
        yield sdk, journal
    finally:
        sdk.close()


def _world(sdk, *, source: bool = False):
    """Two points, a subject, an object and an event — one of each label."""
    p1 = sdk.create_point("claim", "class 4 one", pointKind="statement")
    p2 = sdk.create_point("claim", "class 4 two", pointKind="statement")
    subj = sdk.create_subject("class4-owner")
    obj = sdk.create_object("class4-object")
    ev = sdk.create_event("class4-event", eventKind="decision")
    if source:
        sdk.create_source("https://class4.test/src", sourceKind="document")
    return {
        "p1": p1["id"], "p2": p2["id"],
        "subject": subj["id"], "object": obj["id"],
        "event": ev.get("eventId") or ev.get("id"),
    }


# ════════════════════════════════════════════════════════════════════════════
# 1. The producer — both mints journal, in §4.4's flat shape
# ════════════════════════════════════════════════════════════════════════════


def test_create_edge_journals_the_edge_it_wrote(edge_sdk):
    """`create_edge` — the generic mint — journals the edge (class 4's core)."""
    sdk, journal = edge_sdk
    ids = _world(sdk)
    assert sdk.create_edge("ownedBy", ids["p1"], ids["subject"])["created"] is True

    [rec] = _direct_edges(journal)
    assert rec["edge_type"] == "ownedBy"
    assert rec["src"] == ids["p1"]
    assert rec["tgt"] == ids["subject"]
    assert (rec["source_label"], rec["target_label"]) == ("Point", "Subject")


def test_the_typed_wrappers_journal_through_the_same_statement(edge_sdk):
    """The choke point, not the public door: the three wrappers are covered.

    `create_owned_by` / `create_managed_by` / `create_authored_by` are
    `return self.create_edge(...)`, and `sdk.create_owned_by`-shaped callers that
    reach the projection directly (`sdk.py`'s entity-property path) must journal
    too — so the producer cannot live on `TortoiseSDK.create_edge` alone.
    """
    sdk, journal = edge_sdk
    ids = _world(sdk)
    proj = sdk._get_proj()
    assert proj.create_owned_by(ids["object"], ids["subject"]) is True
    assert proj.create_managed_by(ids["object"], ids["subject"]) is True
    assert proj.create_authored_by(ids["object"], ids["subject"]) is True

    types = [r["edge_type"] for r in _direct_edges(journal)]
    assert types == ["ownedBy", "managedBy", "authoredBy"], types


def test_create_about_edge_journals_too(edge_sdk):
    """The bare about* mint is class 4's other half (`about*` with no
    `EntityLinked` record — the case doc 08 §R5 names as still missing)."""
    sdk, journal = edge_sdk
    ids = _world(sdk)
    assert sdk._get_proj().create_about_edge(
        ids["p1"], ids["object"], "aboutObject") is True

    [rec] = _direct_edges(journal)
    assert rec["edge_type"] == "aboutObject"
    assert rec["src"] == ids["p1"] and rec["tgt"] == ids["object"]
    assert rec["source_label"] == "Point"
    assert rec["target_label"] == "Object"


def test_the_descriptor_is_the_canonical_flat_shape(edge_sdk):
    """§4.4's shape, shared with `DirectEdgeRepoint` — nothing invented.

    In particular NO relationship attributes: these edges carry none (§9.6), and
    a `direction`/`confidence`/`weight` here would be SET on the rebuilt edge by
    pass-2b — a topology the live graph never had.
    """
    sdk, journal = edge_sdk
    ids = _world(sdk)
    sdk.create_edge("dependsOn", ids["p1"], ids["p2"])

    [rec] = _direct_edges(journal)
    assert set(rec) == {
        "event_id", "ts", "type", "initiated_by", "projection_version",
        "id", "src", "tgt", "edge_type", "source_label", "target_label",
    }, sorted(rec)
    assert rec["id"] == f'{ids["p1"]}->{ids["p2"]}:dependsOn'


def test_a_projection_with_no_journal_handle_emits_nothing(tmp_path):
    """The recorder is BOUND, never assumed: a projection alone journals nothing.

    This is the pre-#9553 behaviour, and it is the reason a `rebuild_all` or a
    unit fold over a bare `FalkorProjection` cannot invent records: no journal
    handle exists there to append to.
    """
    sdk = TortoiseSDK(str(tmp_path / "bare.db"))
    try:
        proj = sdk._get_proj()
        assert proj._structural_edge_recorder is None
        p1 = sdk.create_point("claim", "bare one", pointKind="statement")
        p2 = sdk.create_point("claim", "bare two", pointKind="statement")
        assert proj.create_edge(p1["id"], p2["id"], "related") is True
    finally:
        sdk.close()


def test_a_self_edge_is_never_journaled_and_says_so(edge_sdk, caplog):
    """A self-edge would be journaled-but-unreplayable: pass-2b skips `src==tgt`.

    ⛔ AND THE SKIP IS DISCLOSED. The mint DID write the live self-loop, so a
    silent skip is a live ≠ rebuild divergence with no diagnostic anywhere.
    """
    sdk, journal = edge_sdk
    ids = _world(sdk)
    caplog.set_level("WARNING")
    sdk.create_edge("related", ids["p1"], ids["p1"])
    assert _direct_edges(journal) == []
    assert any("SELF-EDGE" in r.message for r in caplog.records), \
        [r.message for r in caplog.records]


# ════════════════════════════════════════════════════════════════════════════
# 2. The endpoint gate — the fold's own probe, and a DECLARED refusal
# ════════════════════════════════════════════════════════════════════════════


def test_the_addressable_label_set_is_the_folds_own():
    """Restated, not guessed: pass-2b sweeps `EPISTEMIC_LABELS` on `p.id`."""
    assert frozenset(EPISTEMIC_LABELS) == RECORD_ADDRESSABLE_EDGE_LABELS


def test_a_source_endpoint_is_declined_and_disclosed(edge_sdk, caplog):
    """`references` from a `:Source` is written to the graph and NOT journaled.

    The fold cannot address a `:Source` (url-keyed, outside the epistemic
    sweep), so journaling it would give the RECORD an edge the JOURNAL rebuild
    can never re-create. The write still happens — the decline is a disclosure,
    not a refusal — and it says so, once, with the reason.
    """
    sdk, journal = edge_sdk
    ids = _world(sdk, source=True)
    caplog.set_level("WARNING")
    assert sdk.create_edge(
        "references", "https://class4.test/src", ids["object"])["created"] is True

    assert _direct_edges(journal) == [], "a Source endpoint must not be journaled"
    assert any("NOT addressable by the `DirectEdgeCreated` replay fold" in r.message
               for r in caplog.records), [r.message for r in caplog.records]
    # the LIVE edge exists — the graph is not the thing being refused
    rows = sdk._get_proj().g.query(
        "MATCH (:Source {url:$u})-[:references]->(:Object {id:$o}) RETURN count(*)",
        params={"u": "https://class4.test/src", "o": ids["object"]},
    ).result_set
    assert rows[0][0] == 1


def test_the_gap_is_disclosed_once_per_relation(edge_sdk, caplog):
    """A per-write line on the `references` fan-out would bury the log."""
    sdk, _journal = edge_sdk
    ids = _world(sdk, source=True)
    sdk.create_object("class4-object-2")
    caplog.set_level("WARNING")
    sdk.create_edge("references", "https://class4.test/src", ids["object"])
    sdk.create_edge("references", "https://class4.test/src", ids["object"])
    hits = [r for r in caplog.records
            if "NOT addressable by the `DirectEdgeCreated` replay fold" in r.message]
    assert len(hits) == 1, [r.message for r in hits]


def test_the_two_declined_causes_do_not_silence_each_other(edge_sdk, caplog):
    """⛔ TWO causes, TWO facts, TWO disclosures — the registry is keyed on both.

    `_RECORD_GAP_WARNED` is the once-per-relation register shared by the mint's
    endpoint decline (`edges.py::_record_structural_edges`, "endpoint NOT
    addressable by the replay fold") and the SDK's self-edge skip
    (`sdk.py::_record_structural_edge`, "SELF-EDGE … E2E-11.6"). Keyed on the
    relation ALONE, either writer's `add(rel)` suppressed the other's `if rel not
    in …`, so a SECOND, DIFFERENT fact was disclosed to nobody.

    Reachable with one relation, no contrivance: a `references` whose source is a
    `:Source` (the documented live case) sets the key, and a subsequent
    `create_edge("references", p, p)` then wrote a live self-loop, journalled
    nothing, and logged nothing — a silent live ≠ rebuild divergence, which is
    exactly what `sdk.py`'s comment on that branch promises cannot happen.

    Vacuous pre-fix: the second assertion finds no self-edge line at all.
    """
    sdk, _journal = edge_sdk
    ids = _world(sdk, source=True)
    caplog.set_level("WARNING")

    # cause 1 — the endpoint decline, on `references`.
    sdk.create_edge("references", "https://class4.test/src", ids["object"])
    declined = [r for r in caplog.records
                if "NOT addressable by the `DirectEdgeCreated` replay fold"
                in r.message]
    assert len(declined) == 1, [r.message for r in caplog.records]

    # cause 2 — a SELF-EDGE, same relation. A DIFFERENT fact about the same rel.
    sdk.create_edge("references", ids["p1"], ids["p1"])
    self_edges = [r for r in caplog.records if "SELF-EDGE" in r.message]
    assert self_edges, (
        "the self-edge skip was SILENCED by the endpoint decline's entry for the "
        "same relation — the register is keyed on the relation alone, so the "
        "live self-loop written here is disclosed to nobody "
        f"(records: {[r.message for r in caplog.records]})")


# ════════════════════════════════════════════════════════════════════════════
# 3. The RECORD consumes it — no new record type, no new column
# ════════════════════════════════════════════════════════════════════════════


def test_the_emitted_record_lands_a_relation_statement():
    """The descriptor is a `relation` row's coordinates, through the EXISTING fold.

    The labels matter: `relation.src_label`/`tgt_label` are bound values and each
    label resolves its endpoint through its own key column (a `Subject` keys
    `name`, not the journal `id`), so a descriptor that named only `Point` would
    resolve an absent endpoint and land no row — the "silently empty" shape.
    """
    record = {
        "type": "DirectEdgeCreated", "ts": "2026-10-23T00:00:00Z",
        "id": "p1->s1:ownedBy", "src": "p1", "tgt": "s1",
        "edge_type": "ownedBy", "source_label": "Point", "target_label": "Subject",
    }
    statements = record_projection.mutations_for(
        record, org_id="o", graph_id="g",
        columns=record_projection.table_columns(),
        refused=record_projection.refused_props_names(),
    )
    relation = [s for s in statements if "INSERT INTO public.relation" in s.sql]
    assert len(relation) == 1, [s.sql for s in statements]
    params = relation[0].params
    assert "ownedBy" in params
    assert "Point" in params and "Subject" in params, params
    # the endpoint joins read the RECORD's own node rows, keyed per label
    assert "(SELECT node_key FROM public.point" in relation[0].sql
    assert "(SELECT node_key FROM public.subject" in relation[0].sql
    # ...and the Subject is matched by its RECORD IDENTITY (`id`), while the
    # KEY it files is the table's canonical one — the reason the label must be
    # in the descriptor at all.
    assert ("FROM public.subject WHERE org_id = %s AND graph_id = %s AND id = %s"
            in relation[0].sql)


def test_the_lane_is_classified_per_relation_and_the_partition_is_total():
    """Carried, or declared uncarried with a reason — nothing in between.

    ⛔ NOT a tautology, deliberately. `RECORD_CARRIED_STRUCTURAL_RELS` is a
    LITERAL and `SOURCE_TARGETED_ABOUT_RELS` is a second literal; this leg is
    what makes adding a predicate to either mint a red test rather than a silent
    widening, and what keeps the ledger honest about the two families that are in
    the lane but can never be journaled.
    """
    assert (frozenset(STRUCTURAL_EDGE_LANE_PREDICATES)
            == RECORD_CARRIED_STRUCTURAL_RELS | SOURCE_TARGETED_ABOUT_RELS), (
        "the carried/uncarried partition no longer covers the lane's write set: "
        f"unclassified={sorted(frozenset(STRUCTURAL_EDGE_LANE_PREDICATES) - RECORD_CARRIED_STRUCTURAL_RELS - SOURCE_TARGETED_ABOUT_RELS)} "
        f"stale={sorted((RECORD_CARRIED_STRUCTURAL_RELS | SOURCE_TARGETED_ABOUT_RELS) - frozenset(STRUCTURAL_EDGE_LANE_PREDICATES))}")
    assert not (RECORD_CARRIED_STRUCTURAL_RELS & SOURCE_TARGETED_ABOUT_RELS)
    assert (STRUCTURAL_EDGE_LANE_PREDICATES
            == _VALID_EDGE_PREDICATES | _VALID_ABOUT_EDGE_PREDICATES)
    for rel in RECORD_CARRIED_STRUCTURAL_RELS:
        assert structural_edge_record_carrier(rel) == "record", rel
    for rel in SOURCE_TARGETED_ABOUT_RELS:
        assert structural_edge_record_carrier(rel) is None, (
            f"`{rel}` targets a :Source, which the pass-2b sweep cannot address — "
            "claiming a record carrier for it would green a family both rebuild "
            "routes still lose")
    assert structural_edge_record_carrier("IMPL") is None
    assert structural_edge_record_carrier("CONTAINS") is None


def test_the_record_carrier_set_covers_the_class_4_families():
    """The issue's own list — named, so a removal is a visible diff."""
    for rel in ("ownedBy", "hasPart", "dependsOn", "wasDerivedFrom", "references",
                "managedBy", "authoredBy", "memberOf", "participatesIn",
                "aboutSubject", "aboutObject", "aboutEvent", "aboutPoint",
                "aboutAction"):
        assert rel in RECORD_CARRIED_STRUCTURAL_RELS, rel
    # ...and the two whose contract target is a :Source are NOT claimed.
    assert "aboutDocument" not in RECORD_CARRIED_STRUCTURAL_RELS
    assert "aboutSource" not in RECORD_CARRIED_STRUCTURAL_RELS


def test_every_carried_predicate_actually_journals(edge_sdk):
    """BEHAVIOURAL, per relation — the leg a derived set can never provide.

    A declaration that is only compared against itself cannot catch a producer
    that emits nothing for one relation. This writes EVERY carried predicate with
    addressable endpoints and asserts one `DirectEdgeCreated` per predicate, so a
    predicate that reaches an allow-list but not the recorder is red here.
    """
    sdk, journal = edge_sdk
    ids = _world(sdk)
    proj = sdk._get_proj()
    before = len(_direct_edges(journal))

    # `create_edge` takes the rest — including `aboutAction`, whose target is a
    # Point and which the about MINT does not accept.
    generic = sorted(RECORD_CARRIED_STRUCTURAL_RELS - ABOUT_RELS)
    for rel in generic:
        assert proj.create_edge(ids["p1"], ids["p2"], rel) is True, rel

    # `create_about_edge` is the about mint; each rel needs its own target label
    # (the ONTOLOGY §3.2 triples, not a guess).
    for rel in sorted(RECORD_CARRIED_STRUCTURAL_RELS & ABOUT_RELS):
        target = ids[ABOUT_TARGETS[rel]]
        assert proj.create_about_edge(ids["p1"], target, rel) is True, rel

    emitted = [r["edge_type"] for r in _direct_edges(journal)[before:]]
    assert sorted(emitted) == sorted(RECORD_CARRIED_STRUCTURAL_RELS), {
        "not journaled": sorted(RECORD_CARRIED_STRUCTURAL_RELS - set(emitted)),
        "journaled but not declared": sorted(set(emitted) - RECORD_CARRIED_STRUCTURAL_RELS),
    }


def test_a_source_targeted_about_edge_is_declined_and_its_family_is_not_claimed(
        edge_sdk, caplog):
    """`aboutDocument`/`aboutSource` are in the lane and can NEVER be journaled.

    Their contract target is a document/provenance `:Source`, which the pass-2b
    sweep cannot address, so this is not a per-write caveat like `references` —
    it is 100% of the family. The ledger must say so (it is NOT in
    `RECORD_CARRIED_STRUCTURAL_RELS`), the write must still land, and the decline
    must be disclosed.
    """
    sdk, journal = edge_sdk
    ids = _world(sdk, source=True)
    caplog.set_level("WARNING")
    doc = sdk.create_document("class4-doc", documentKind="page")
    doc_id = doc.get("url") or doc.get("id")
    assert sdk._get_proj().create_about_edge(
        ids["p1"], doc_id, "aboutDocument") is True

    assert [r["edge_type"] for r in _direct_edges(journal)
            if r["edge_type"] == "aboutDocument"] == []
    assert any("NOT addressable by the `DirectEdgeCreated` replay fold" in r.message
               for r in caplog.records), [r.message for r in caplog.records]
    assert structural_edge_record_carrier("aboutDocument") is None


# ════════════════════════════════════════════════════════════════════════════
# 4. The JOURNAL rebuild carries it — the engine's fold, unchanged
# ════════════════════════════════════════════════════════════════════════════


def test_the_journal_rebuild_restores_the_class_4_topology(edge_sdk, tmp_path):
    """End to end on the journal route: write → `rebuild_all` → the edges exist.

    This is the leg that was `edges: 0` before the producer landed, and it is the
    same fold the record route relies on — proving both routes read ONE carrier.
    """
    sdk, _journal = edge_sdk
    ids = _world(sdk)
    for rel, src, tgt in (
        ("ownedBy", ids["p1"], ids["subject"]),
        ("dependsOn", ids["p1"], ids["p2"]),
        ("wasDerivedFrom", ids["p2"], ids["p1"]),
        ("hasPart", ids["p1"], ids["p2"]),
        ("references", ids["p1"], ids["object"]),
    ):
        assert sdk.create_edge(rel, src, tgt)["created"] is True, rel
    recovered = sdk._get_proj()
    # the journal was written by THIS sdk; rebuild from a wiped graph
    report = recovered.rebuild_all(str(tmp_path), confirm_destructive=True)
    assert report is not None

    types1 = {row[0] for row in recovered.g.query(
        "MATCH (:Point {id:$p})-[r]->() RETURN type(r)",
        params={"p": ids["p1"]}).result_set}
    assert {"ownedBy", "dependsOn", "hasPart", "references"} <= types1, types1
    types2 = {row[0] for row in recovered.g.query(
        "MATCH (:Point {id:$p})-[r]->() RETURN type(r)",
        params={"p": ids["p2"]}).result_set}
    assert "wasDerivedFrom" in types2, types2
    assert {
        row[0] for row in recovered.g.query(
            "MATCH (:Point {id:$p})-[r:dependsOn]->(:Point {id:$q}) "
            "RETURN type(r)", params={"p": ids["p1"], "q": ids["p2"]}
        ).result_set
    } == {"dependsOn"}


# ════════════════════════════════════════════════════════════════════════════
# 5. A REPLAY never journals — recovery reads the log, it does not extend it
# ════════════════════════════════════════════════════════════════════════════


def test_a_rebuild_does_not_append_to_the_journal_it_replays(edge_sdk, tmp_path):
    """`rebuild_all` re-wires about links through `create_about_edge` — on the
    SDK's OWN bound projection — so without a replay gate a recovery WRITE would
    append to the append-only journal: one record per journaled link, per rebuild,
    unbounded, built from the endpoints resolved AT REPLAY TIME.

    Measured before the gate: a 4-record journal re-grew to 5, 6, 7 over three
    successive rebuilds. This leg pins the line count across two runs.
    """
    sdk, journal = edge_sdk
    ids = _world(sdk)
    proj = sdk._get_proj()
    assert proj.create_about_edge(ids["p1"], ids["object"], "aboutObject") is True
    live_lines = len(journal.read_text().splitlines())
    live_edges = len(_direct_edges(journal))

    proj.rebuild_all(str(tmp_path), confirm_destructive=True)
    after_first = len(journal.read_text().splitlines())
    proj.rebuild_all(str(tmp_path), confirm_destructive=True)
    after_second = len(journal.read_text().splitlines())

    assert (after_first, after_second) == (live_lines, live_lines), (
        "a rebuild appended to the journal it was replaying "
        f"({live_lines} → {after_first} → {after_second} lines)")
    assert len(_direct_edges(journal)) == live_edges


# ════════════════════════════════════════════════════════════════════════════
# 6. A SUPERSEDE must not cost the edge — the fold's skip is gated on the
#    rels a supersede actually MOVES
# ════════════════════════════════════════════════════════════════════════════


def test_the_supersede_transfer_set_matches_the_writer():
    """The fold's mirror of the writer's own `SUPERSEDE_STRUCTURAL_RELS`.

    The fold cannot import `tortoise.sdk` (cycle), so the set is mirrored —
    and pinned here, so a rel added to the writer's transfer cannot silently
    stop being skipped on replay (or start being skipped when it is not moved).
    """
    from tortoise.projection import _supersede_moved_rels
    from tortoise.sdk import SUPERSEDE_STRUCTURAL_RELS

    assert frozenset(SUPERSEDE_STRUCTURAL_RELS) == SUPERSEDE_TRANSFERRED_RELS
    assert (_supersede_moved_rels()
            == frozenset({"IMPL", "NAND"}) | SUPERSEDE_TRANSFERRED_RELS)


def test_a_supersede_does_not_drop_a_structural_edge_the_live_graph_kept(edge_sdk):
    """⛔ THE GUARD'S PREMISE, measured.

    `DirectEdgeCreated`'s fold skipped every creation whose endpoint had been
    superseded, arguing that supersede had already moved the edge and journaled
    a repoint. That is true only for the rels supersede MOVES — for
    `dependsOn`/`ownedBy`/… it never touches the edge, so LIVE keeps it at the
    old point and the skip made `rebuild_all` drop it. Measured before the gate:
    `dependsOn` live at old, rebuild → gone.
    """
    sdk, _journal = edge_sdk
    ids = _world(sdk)
    proj = sdk._get_proj()
    assert proj.create_edge(ids["p1"], ids["p2"], "dependsOn") is True
    successor = sdk.create_point("claim", "class 4 successor", pointKind="statement")
    sdk.supersede_point(ids["p1"], successor["id"])

    def outgoing(pid: str) -> set[str]:
        return {row[0] for row in proj.g.query(
            "MATCH (:Point {id:$p})-[r]->() RETURN type(r)",
            params={"p": pid}).result_set}

    live_old = outgoing(ids["p1"])
    assert "dependsOn" in live_old, live_old
    proj.rebuild_all(str(edge_sdk[1].parent), confirm_destructive=True)
    assert outgoing(ids["p1"]) == live_old, (
        "a rebuild dropped an edge the live graph still holds at the superseded "
        "point — the fold's supersede skip was applied to a rel supersede never moves")
