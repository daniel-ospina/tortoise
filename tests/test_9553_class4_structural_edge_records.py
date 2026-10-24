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
    SOURCE_ENDPOINT_CANONICAL_RELS,
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
    # `aboutDocument` is in here because the about MINT is the only verb that can
    # journal it between epistemic endpoints (`create_edge` type-checks its target
    # and a document is always a `:Source`, which the fold cannot address).
    "aboutDocument",
})
ABOUT_TARGETS = {
    "aboutSubject": "subject",
    "aboutObject": "object",
    "aboutEvent": "event",
    "aboutPoint": "p2",
    "aboutDocument": "p2",
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
    LITERAL and `SOURCE_ENDPOINT_CANONICAL_RELS` is a second literal; this leg is
    what makes adding a predicate to either mint a red test rather than a silent
    widening, and what keeps the ledger honest about the two families that are in
    the lane but can never be journaled.
    """
    assert (frozenset(STRUCTURAL_EDGE_LANE_PREDICATES)
            == RECORD_CARRIED_STRUCTURAL_RELS), (
        "the carried set no longer covers the lane's write set: "
        f"uncarried={sorted(frozenset(STRUCTURAL_EDGE_LANE_PREDICATES) - RECORD_CARRIED_STRUCTURAL_RELS)} "
        f"stale={sorted(RECORD_CARRIED_STRUCTURAL_RELS - frozenset(STRUCTURAL_EDGE_LANE_PREDICATES))}")
    assert (STRUCTURAL_EDGE_LANE_PREDICATES
            == _VALID_EDGE_PREDICATES | _VALID_ABOUT_EDGE_PREDICATES)
    for rel in RECORD_CARRIED_STRUCTURAL_RELS:
        assert structural_edge_record_carrier(rel) == "record", rel
    # ⛔ The Source-canonical subset is NAMED, not excluded: measured, both are
    # journaled when their endpoints are epistemic (`create_edge` type-checks only
    # `aboutDocument`; `create_about_edge` type-checks neither), so excluding them
    # from the ledger was wrong in both directions (peer review, cycle 4).
    assert SOURCE_ENDPOINT_CANONICAL_RELS <= RECORD_CARRIED_STRUCTURAL_RELS
    for rel in SOURCE_ENDPOINT_CANONICAL_RELS:
        assert structural_edge_record_carrier(rel) == "record", rel
    assert structural_edge_record_carrier("IMPL") is None
    assert structural_edge_record_carrier("CONTAINS") is None


def test_the_record_carrier_set_covers_the_class_4_families():
    """The issue's own list — named, so a removal is a visible diff."""
    for rel in ("ownedBy", "hasPart", "dependsOn", "wasDerivedFrom", "references",
                "managedBy", "authoredBy", "memberOf", "participatesIn",
                "aboutSubject", "aboutObject", "aboutEvent", "aboutPoint",
                "aboutAction", "aboutDocument", "aboutSource"):
        assert rel in RECORD_CARRIED_STRUCTURAL_RELS, rel


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
    # The PREDICATE is carried (the producer exists); this WRITE was declined.
    assert structural_edge_record_carrier("aboutDocument") == "record"


def test_an_aboutSource_between_epistemic_endpoints_IS_journaled(edge_sdk):
    """The ledger's honesty leg for a peer-review finding (cycle 4).

    `create_edge` type-checks only `aboutDocument`'s target, and
    `create_about_edge` type-checks none — so `aboutSource` (and
    `aboutDocument`) between two epistemic endpoints really is journaled, which
    is why excluding them from the carried set was wrong. The DECLINE belongs to
    the `:Source`-endpoint case, not to the predicate.
    """
    sdk, journal = edge_sdk
    ids = _world(sdk)
    assert sdk._get_proj().create_edge(ids["p1"], ids["p2"], "aboutSource") is True
    [rec] = [r for r in _direct_edges(journal)
             if r["edge_type"] == "aboutSource"]
    assert (rec["source_label"], rec["target_label"]) == ("Point", "Point")
    assert structural_edge_record_carrier("aboutSource") == "record"


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
    from tortoise.projection import SUPERSEDE_DIRECT_RELS, _supersede_moved_rels
    from tortoise.sdk import SUPERSEDE_STRUCTURAL_RELS

    assert frozenset(SUPERSEDE_STRUCTURAL_RELS) == SUPERSEDE_TRANSFERRED_RELS
    assert frozenset({"IMPL", "NAND"}) == SUPERSEDE_DIRECT_RELS
    assert _supersede_moved_rels() == SUPERSEDE_DIRECT_RELS | SUPERSEDE_TRANSFERRED_RELS


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


def test_a_supersede_does_not_drop_an_INCOMING_structural_edge(edge_sdk):
    """Leg 2b moves only what is OUTGOING from the superseded point.

    `(other)-[aboutPoint]-> old` is never moved by a supersede, so live keeps it
    at the old point — a rel-type-only gate skipped it and the rebuild lost it
    (measured: live `p2->p1 = ['aboutPoint']`, rebuild `[]`).
    """
    sdk, _journal = edge_sdk
    ids = _world(sdk)
    proj = sdk._get_proj()
    assert proj.create_about_edge(ids["event"], ids["p1"], "aboutPoint") is True
    successor = sdk.create_point("claim", "incoming successor", pointKind="statement")
    sdk.supersede_point(ids["p1"], successor["id"])

    def incoming(pid: str) -> set[str]:
        return {row[0] for row in proj.g.query(
            "MATCH ()-[r]->(:Point {id:$p}) RETURN type(r)",
            params={"p": pid}).result_set}

    live = incoming(ids["p1"])
    # (`CORRECTS` is the supersede's own successor edge, pointing IN as well.)
    assert "aboutPoint" in live, live
    proj.rebuild_all(str(_journal.parent), confirm_destructive=True)
    assert incoming(ids["p1"]) == live, (
        "a rebuild dropped an INCOMING edge the live graph keeps at the "
        "superseded point — leg 2b moves outgoing edges only")


def test_an_edge_minted_AT_a_superseded_point_is_replayed(edge_sdk):
    """The gate is an ORDERING test, not a rel-type one.

    `create_edge`/`create_about_edge` have no terminal-endpoint guard (unlike
    `create_direct_edge`'s #2422 refusal), so an edge can be minted at an
    already-superseded id. It is kept live at the old point, journaled as a plain
    creation, and supersede never moves it (it did not exist then) — so a gate
    that skipped on `src in succ` alone dropped it (measured: live kept it,
    rebuild lost it, no repoint descriptor anywhere).
    """
    sdk, _journal = edge_sdk
    ids = _world(sdk)
    proj = sdk._get_proj()
    successor = sdk.create_point("claim", "post-supersede successor", pointKind="statement")
    sdk.supersede_point(ids["p1"], successor["id"])
    # ...minted AFTER the supersede, at the now-terminal id.
    assert proj.create_edge(ids["p1"], ids["p2"], "dependsOn") is True

    def outgoing(pid: str) -> set[str]:
        return {row[0] for row in proj.g.query(
            "MATCH (:Point {id:$p})-[r]->() RETURN type(r)",
            params={"p": pid}).result_set}

    live = outgoing(ids["p1"])
    assert "dependsOn" in live, live
    proj.rebuild_all(str(_journal.parent), confirm_destructive=True)
    assert outgoing(ids["p1"]) == live, (
        "a rebuild dropped an edge minted AFTER its point was superseded — the "
        "supersede never moved it, so the creation must be replayed")


def test_the_supersede_gate_is_an_ordering_test_not_a_rel_type_test():
    """The PREDICATE, as a table — the comparison nothing else reached.

    ⛔ THE GAP THIS CLOSES (#9835 review, P2). Every behavioural leg checked
    before this one exits ahead of `ev_seq < superseded_at[0]`: the `dependsOn`
    legs at `etype not in _supersede_moved_rels()`, the incoming `aboutPoint`
    leg at `src not in succ`. So the two answers that make this gate an ORDERING
    test (skip only what supersede actually MOVED) were pinned by nothing — a
    gate reduced to `src in succ`, or with the comparison inverted, passed the
    whole file. Both are red here (measured by mutation, then reverted).

    The table is the contract, so it is asserted as one:

      * a DIRECT rel (`IMPL`/`NAND`) is moved in BOTH directions by 2a, and 2a
        always journals a repoint ⇒ a superseded endpoint on either side means
        moved, regardless of ordering;
      * a TRANSFERRED structural rel is moved by 2b OUTGOING only, at supersede
        time ⇒ moved iff `src in succ` AND the creation preceded the supersede;
      * a rel outside the moved set is never moved by any leg ⇒ never skipped.
    """
    from tortoise.projection import SUPERSEDE_DIRECT_RELS, _supersede_took_this_edge

    dead, other = "p-dead", "p-other"
    succ = {dead: "p-successor"}
    # The supersede ran at seq 9; the creations below are at 5 (before) / 10 (after).
    supersede_last = {dead: (9, {"id": dead})}

    def moved(etype, ev_seq, src, tgt):
        return _supersede_took_this_edge(
            etype, ev_seq, src, tgt, succ, supersede_last)

    assert frozenset({"IMPL", "NAND"}) == SUPERSEDE_DIRECT_RELS
    # 2a — both directions, ordering irrelevant.
    for rel in SUPERSEDE_DIRECT_RELS:
        assert moved(rel, 5, dead, other) is True
        assert moved(rel, 10, other, dead) is True
    # 2b — OUTGOING only, and only when the edge existed at supersede time.
    for rel in ("aboutPoint", "aboutSubject", "extractedFrom", "wasDerivedFrom"):
        assert moved(rel, 5, dead, other) is True, rel        # preceded ⇒ moved
        assert moved(rel, 10, dead, other) is False, rel      # followed ⇒ kept at dead
        assert moved(rel, 5, other, dead) is False, rel       # INCOMING ⇒ never moved
    # Outside the moved set: supersede touches nothing, so nothing is skipped.
    for rel in ("dependsOn", "ownedBy", "hasPart", "references"):
        assert moved(rel, 5, dead, other) is False, rel
        assert moved(rel, 5, other, dead) is False, rel
    # A supersede this pass did not keep: not "did not move" — UNKNOWN, so the
    # creation is skipped (the conservative answer the gate's own docstring names).
    assert _supersede_took_this_edge(
        "aboutPoint", 10, dead, other, succ, {}) is True


def test_a_moved_rel_minted_BEFORE_the_supersede_is_skipped_at_the_dead_point(
        edge_sdk):
    """The `True` half of the ordering branch: creation PRECEDED the supersede.

    ⛔ THE GAP THIS CLOSES (#9835 review, P2). `_supersede_took_this_edge`'s
    moved-rel half is `src in succ` **and** `ev_seq < superseded_at[0]`, and no
    leg reached the comparison: the `dependsOn` legs return at
    `etype not in _supersede_moved_rels()`, and the `aboutPoint` leg above has
    `src` = the EVENT (an incoming edge), so it returns at `src not in succ`.
    Both halves of `src in succ AND created-first` were therefore unpinned — for
    the very rels the PR calls a residual (`wasDerivedFrom`, `aboutAction`).

    `aboutPoint` is in `DERIVABLE_STRUCTURAL_RELS`, so supersede leg 2b MOVES
    this edge to the successor live AND journals a `DirectEdgeRepoint` for it.
    Replaying the creation at the dead source on top of that leg would leave a
    phantom at the old point — the divergence `E2E-11.6` forbids, and the
    direction Interface A forbids (the graph RICHER than live).

    The target is created WITH a `name`, deliberately: `aboutPoint`'s replay key
    in the transfer descriptor is the target's `name` (`stub_key`), so a
    name-less Point target has no replayable descriptor at all, 2b would move the
    edge live while journaling nothing, and this leg would then pass for a
    pre-existing #2489 reason instead of for the gate.

    ⚠️ HONEST LIMIT, measured (comparison inverted, then reverted): this leg
    STILL PASSES with the ordering comparison inverted, because the 2b replay leg
    runs AFTER the creation fold and its delete-old leg scrubs the phantom the
    fold just minted at the dead point. So what this leg pins is the ROUND TRIP
    (live == rebuilt at the successor, nothing at the dead point), not the
    comparison — the comparison is pinned by
    `test_the_supersede_gate_is_an_ordering_test_not_a_rel_type_test` above and
    by the AFTER leg below. Stated here so the next reader does not mistake it
    for a discriminating leg.
    """
    sdk, journal = edge_sdk
    ids = _world(sdk)
    proj = sdk._get_proj()
    target = sdk.create_point("statement", "moved target", name="moved-target")["id"]
    # BEFORE the supersede: this edge exists when 2b runs, so 2b moves it.
    assert proj.create_about_edge(ids["p1"], target, "aboutPoint") is True
    successor = sdk.create_point("claim", "moved successor", pointKind="statement")
    sdk.supersede_point(ids["p1"], successor["id"])

    def outgoing(pid: str) -> set[str]:
        return {row[0] for row in proj.g.query(
            "MATCH (:Point {id:$p})-[r]->() RETURN type(r)",
            params={"p": pid}).result_set}

    live_old, live_new = outgoing(ids["p1"]), outgoing(successor["id"])
    assert "aboutPoint" not in live_old, (
        "premise: 2b moves an aboutPoint that existed at supersede time, so "
        f"live holds it at the successor, not at the dead point ({live_old})")
    assert "aboutPoint" in live_new, live_new
    # ...and the transfer was JOURNALED, so the successor copy is recoverable.
    assert [r for r in _direct_edges(journal) if r["edge_type"] == "aboutPoint"]

    proj.rebuild_all(str(journal.parent), confirm_destructive=True)

    assert "aboutPoint" not in outgoing(ids["p1"]), (
        "a rebuild replayed a creation the supersede MOVED, leaving a phantom "
        "at the dead point the live graph does not hold")
    assert outgoing(successor["id"]) == live_new


def test_a_moved_rel_minted_AFTER_the_supersede_is_replayed_at_the_old_point(
        edge_sdk):
    """The `False` half — same rel, same superseded source, ordering INVERTED.

    ⛔ THE GAP THIS CLOSES (#9835 review, P2): `aboutPoint` is in the moved set
    AND `src in succ`, so this is the only shape that reaches
    `ev_seq < superseded_at[0]` and answers **False**. The `dependsOn` leg above
    proves the same OUTCOME through the rel-type early return, so nothing pinned
    the comparison itself: a gate that answered `src in succ` alone, or that
    inverted the comparison, would pass every other leg in this file.

    Live keeps the edge at the dead point (2b already ran, and it moves only
    what existed then), so the rebuild must re-create it there.
    """
    sdk, _journal = edge_sdk
    ids = _world(sdk)
    proj = sdk._get_proj()
    successor = sdk.create_point("claim", "later successor", pointKind="statement")
    sdk.supersede_point(ids["p1"], successor["id"])
    # AFTER the supersede, at the now-terminal id (`create_about_edge` has no
    # terminal-endpoint guard, unlike `create_direct_edge`'s #2422 refusal).
    assert proj.create_about_edge(ids["p1"], ids["p2"], "aboutPoint") is True

    def outgoing(pid: str) -> set[str]:
        return {row[0] for row in proj.g.query(
            "MATCH (:Point {id:$p})-[r]->() RETURN type(r)",
            params={"p": pid}).result_set}

    live_old = outgoing(ids["p1"])
    assert "aboutPoint" in live_old, live_old

    proj.rebuild_all(str(_journal.parent), confirm_destructive=True)

    assert "aboutPoint" in outgoing(ids["p1"]), (
        "a rebuild dropped a moved-set edge minted AFTER the supersede — the "
        "supersede never moved it, so the ordering comparison must answer False"
    )
