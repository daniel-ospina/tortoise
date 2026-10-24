"""#9553 (R5, class 4) — the SDK's structural-edge door must CARRY its edge.

`tortoise/projection/edges.py::_EdgeHandlers.create_edge` mints
`MATCH (s:L {k:$sv}) MATCH (t:M {k:$tv}) MERGE (s)-[:<predicate>]->(t)` — the
lane the SDK edge door and its three typed wrappers
(`create_owned_by`/`create_managed_by`/`create_authored_by`) funnel onto. It
emits **no journal record itself** (and the typed wrappers are called on the
projection from `sdk.py`'s entity wiring, so they never reach the door), so
before #9553 every structural predicate that is not snapshot-carried was GONE
after a rebuild.

That gap was disclosed at the point of the gap — the pre-#9553
`record_projection.py` text ("… which have a working `DirectEdgeCreated` fold
and NO producer (class 4 — a producer-side change in `sdk.py`, not a projection
one)") — and is now stated, with its bounds, in `projection/edges.py`'s
declaration block. This suite is its falsifier:

The two halves the R5 target needs are therefore:

  (a) the write is JOURNALLED — a `DirectEdgeCreated` record carrying the
      **resolved node ids** (the fold is id-anchored, so a record written with
      the caller's spelling of a name/url endpoint would be silently skipped at
      replay: a false green), one per edge actually minted; and
  (b) the edge SURVIVES `rebuild_all` from that journal alone.

⛔ What the record must name. The fold resolves endpoints by `id`, while this
verb's resolver accepts an OR-set (`id` | `eventId` | `url`). Today `:Source`
`id == url` and `:Event` `id == eventId`, so the two spellings coincide — the
emission resolves the node `id` anyway so the record cannot silently become
unresolvable if that widens. The parts that do NOT coincide are pinned as
MEASURED BOUNDS at the bottom of this file (`test_bound_*`: the typed-wrapper /
node-property lane, an id-bearing `:Source` endpoint, a URL-only `:Source` stub
that carries no `id` at all, a self-edge, and an edge at an endpoint that is
later superseded), because a bound asserted in prose is a bound nobody
re-checks.

Offline: an embedded store (explicit `db_path`) plus a JSONL journal — no
network, no provider spend.
"""
from __future__ import annotations

import pytest

from tortoise.log import EventLog
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def env(tmp_path):
    """A real SDK with a configured JSONL journal (the replay source)."""
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "edges9553.db"),
                      event_log_path=str(events / "events.jsonl"))
    try:
        yield sdk, events
    finally:
        sdk.close()


def _journal(events) -> list[dict]:
    return EventLog(str(events / "events.jsonl")).read_all()


def _edge_count(proj, rel: str, src: str, tgt: str) -> int:
    """Edges of ``rel`` between the two nodes named by their ``id`` property.

    Deliberately id-anchored: that is the resolution the pass-2b fold performs,
    so a count here answers "did replay put the edge back where the record
    points", not "did some name-shaped match happen to land".
    """
    rows = proj.g.query(
        f"MATCH (s {{id:$src}})-[r:{rel}]->(t {{id:$tgt}}) RETURN count(r)",
        params={"src": src, "tgt": tgt},
    ).result_set
    return int(rows[0][0]) if rows else 0


def test_the_structural_edge_is_journaled_and_survives_the_rebuild(env):
    """The issue's own target: a record carrier exists AND the census matches."""
    sdk, events = env
    team = sdk.create_subject("Team Atlas")
    widget = sdk.create_object("Widget X", "product")

    res = sdk.create_edge("ownedBy", widget["id"], team["id"])
    assert res["created"] is True

    proj = sdk._get_proj()
    assert _edge_count(proj, "ownedBy", widget["id"], team["id"]) == 1, (
        "the live write did not land — the rest of this test would be vacuous")

    # (a) the carrier exists.
    created = [e for e in _journal(events) if e.get("type") == "DirectEdgeCreated"]
    assert created, (
        "`sdk.create_edge` emitted NO DirectEdgeCreated record, so the edge is "
        "live-only: the replay engine's pass-2b fold has nothing to re-create "
        "(#9553 R5 class 4 — 'no producer'). The graph and the record disagree "
        "and a rebuild silently loses the edge.")
    carriers = [
        e for e in created
        if e.get("src") == widget["id"] and e.get("tgt") == team["id"]
        and e.get("edge_type") == "ownedBy"
    ]
    assert carriers, (
        f"a DirectEdgeCreated record was emitted but none carries this edge: "
        f"expected src={widget['id']!r} tgt={team['id']!r} "
        f"edge_type='ownedBy'; got {created!r}")

    # (b) the edge survives a journal-sourced rebuild.
    proj.rebuild_all(str(events), confirm_destructive=True)
    assert _edge_count(proj, "ownedBy", widget["id"], team["id"]) == 1, (
        "`rebuild_all` from the journal LOST the `ownedBy` edge. Before the "
        "fix this suite measured exactly that (`edges: 0`); a rebuild that "
        "returns success while dropping the record's topology is the "
        "record-is-poorer-than-the-graph divergence Interface A forbids.")


@pytest.mark.parametrize("relation", ["dependsOn", "references", "hasPart"])
def test_every_structural_predicate_of_the_door_survives(env, relation):
    """The door is ONE statement shape for every allowlisted predicate, so the
    carrier must not be wired for `ownedBy` alone."""
    sdk, events = env
    team = sdk.create_subject("Team Atlas")
    widget = sdk.create_object(f"Widget {relation}", "product")

    assert sdk.create_edge(relation, widget["id"], team["id"])["created"] is True
    proj = sdk._get_proj()
    assert _edge_count(proj, relation, widget["id"], team["id"]) == 1

    proj.rebuild_all(str(events), confirm_destructive=True)
    assert _edge_count(proj, relation, widget["id"], team["id"]) == 1, (
        f"the `{relation}` edge was dropped by a journal-sourced rebuild — the "
        f"record does not carry it (#9553 class 4)")


def test_the_carrier_shape_is_the_folds_flat_descriptor(env):
    """The record must be the pass-2b fold's flat `{src, tgt, edge_type,
    direction}` shape naming the ROLLED-UP node id — not a nested snapshot the
    fold cannot read, and not the caller's spelling of an endpoint.

    (Today `id == url` for a `:Source` and `id == eventId` for an `:Event`, so
    the two spellings coincide; the assertion is written against the resolved
    node id anyway, because the fold resolves by `id` and a future producer
    that widens the resolve set must not silently lose edges.)
    """
    sdk, events = env
    team = sdk.create_subject("Team Atlas")
    widget = sdk.create_object("Widget Shaped", "product")
    sdk.create_edge("ownedBy", widget["id"], team["id"])

    created = [e for e in _journal(events) if e.get("type") == "DirectEdgeCreated"]
    assert created, "no carrier emitted"
    rec = created[0]
    assert (rec.get("src"), rec.get("tgt"), rec.get("edge_type")) == (
        widget["id"], team["id"], "ownedBy"), (
        f"the carrier is not the fold's flat descriptor: {rec!r}")
    assert rec.get("id") == f"{widget['id']}->{team['id']}:ownedBy"
    # ⛔ NO attrs: the live write mints a BARE relationship, and the fold sets
    # only the attrs the record carries — a `direction` here would make the same
    # edge `{}` live and `{direction:'out'}` post-rebuild.
    for attr in ("direction", "confidence", "weight", "label", "batch_id"):
        assert attr not in rec, (
            f"the carrier carries `{attr}`, which the live write never sets — "
            f"that is a live/replay divergence: {rec!r}")

    # …and the rebuilt edge matches the live one property-for-property.
    proj = sdk._get_proj()
    live_props = proj.g.query(
        "MATCH (s {id:$src})-[r:ownedBy]->(t {id:$tgt}) RETURN properties(r)",
        params={"src": widget["id"], "tgt": team["id"]},
    ).result_set[0][0]
    proj.rebuild_all(str(events), confirm_destructive=True)
    rebuilt_props = proj.g.query(
        "MATCH (s {id:$src})-[r:ownedBy]->(t {id:$tgt}) RETURN properties(r)",
        params={"src": widget["id"], "tgt": team["id"]},
    ).result_set[0][0]
    assert dict(live_props) == dict(rebuilt_props), (
        f"live and rebuilt edge attrs diverge: {dict(live_props)!r} vs "
        f"{dict(rebuilt_props)!r}")


def test_a_dedup_hit_still_emits(env):
    """Emit on EVERY write, not created-only.

    The `MERGE` is idempotent, so a second identical call mints nothing — but it
    must still emit, because that is what closes the crash window between the
    write landing and its record being appended (`create_direct_edge` states the
    same contract for this record type). A created-only gate would lose the edge
    from the log forever if the process died in that window.

    ⛔ The premise has to be OBSERVABLE, or this test passes for a contract it
    does not measure (#9553 review P2). `if created:` is the gate in `sdk.py`,
    and for THIS verb `created` is not a creation flag: `_EdgeHandlers.
    create_edge` returns `MERGE … RETURN count(*) > 0`, truthy on a dedup hit
    too. So the second call must be shown to be a DEDUP — the edge count is
    still 1 while its `created` flag is `True` — and both observations are
    asserted here. Without the count, a regression that minted a PARALLEL edge
    on the second call would leave this green too.
    """
    sdk, events = env
    team = sdk.create_subject("Team Atlas")
    widget = sdk.create_object("Widget Dedup", "product")
    proj = sdk._get_proj()

    assert sdk.create_edge("ownedBy", widget["id"], team["id"])["created"] is True
    assert _edge_count(proj, "ownedBy", widget["id"], team["id"]) == 1
    assert sdk.create_edge("ownedBy", widget["id"], team["id"])["created"] is True
    assert _edge_count(proj, "ownedBy", widget["id"], team["id"]) == 1, (
        "the second call minted a SECOND edge — the `MERGE` is not idempotent "
        "here, so this test is not measuring a dedup hit at all")
    carriers = [
        e for e in _journal(events)
        if e.get("type") == "DirectEdgeCreated"
        and e.get("src") == widget["id"] and e.get("tgt") == team["id"]
    ]
    assert len(carriers) == 2, (
        f"a `MERGE` dedup-hit emitted no carrier — the crash window is not "
        f"closed (got {len(carriers)} of 2): {carriers!r}")


# ── MEASURED BOUNDS — the lanes this emission does NOT reach ──────────────
#
# These FIVE assert the CURRENT behaviour, not a requirement. Each one is a
# live-only edge that a rebuild drops. They are tests (not prose) so that the
# day the missing half lands, this file reds and the claim above it has to be
# re-derived with the change rather than discovered later.


def test_bound_an_id_bearing_source_endpoint_is_not_restored(env):
    """BOUND (1 of 2 for `:Source`): the record IS emitted, but the fold's id
    sweep covers `EPISTEMIC_LABELS` only, so a `:Source` endpoint is skipped at
    replay.

    `aboutSource`/`aboutDocument` targets are `:Source` by definition, so those
    two relations can never be restored by this record without widening the
    fold's sweep.

    ⛔ The record's PRESENCE is asserted, not just its failure to restore: this
    test's own docstring says the carrier is written, and a claim stated in
    prose and unchecked in the test that states it is exactly the shape the
    `url`-only stub below was missed through (#9553 review P2).
    """
    sdk, events = env
    team = sdk.create_subject("Team Atlas")
    sdk.create_source("https://docs9553.example/page", "document")

    assert sdk.create_edge(
        "references", team["id"], "https://docs9553.example/page")["created"] is True

    proj = sdk._get_proj()
    live = proj.g.query(
        "MATCH (s:Subject)-[r:references]->(t:Source) RETURN count(r)").result_set[0][0]
    assert live == 1
    carriers = [
        e for e in _journal(events)
        if e.get("type") == "DirectEdgeCreated" and e.get("edge_type") == "references"
    ]
    assert carriers, (
        "no `DirectEdgeCreated` was emitted for a `references` edge to an "
        "ID-BEARING `:Source` (`create_source` writes `id = coalesce($id, "
        "$url)`, so the emission has an id to name and MUST write a record) — "
        "the failure mode this bound describes is 'the fold skips it', not "
        "'nothing is journaled'")
    proj.rebuild_all(str(events), confirm_destructive=True)
    rebuilt = proj.g.query(
        "MATCH (s:Subject)-[r:references]->(t:Source) RETURN count(r)").result_set[0][0]
    assert rebuilt == 0, (
        "the fold now restores a `:Source` endpoint — the emission's bound has "
        "been closed, so the claim in projection/edges.py and record_projection.py "
        "must be widened (and this bound test updated with it)")


def test_bound_a_url_only_source_stub_emits_no_record_and_reports_it(env):
    """BOUND (2 of 2 for `:Source`): a stub `:Source` minted by
    `_mint_source_stub` carries NO `id` property, so the emission has no
    identity to name and writes nothing — a DIFFERENT mechanism from the bound
    above (record emitted, fold skips it) and from the self-edge (deliberate).

    ⛔ The edge IS minted live: the source OR-set carries `by_url=True`
    precisely so a url-only stub resolves. An edge minted live with nothing
    journaled is a data-loss signal, not an ordinary absent identity — so the
    skip must be REPORTED (`projection._log_identity_skip`,
    `absent_is_ordinary=False, drops_record=True`) rather than dropped in
    silence, which is what `#9553`'s review P2 found.
    """
    from tortoise.projection.nonfolded import collect_identity_skips

    sdk, events = env
    widget = sdk.create_object("Widget Stub Source", "product")
    stub_url = "https://docs9553.example/url-only-stub"
    # `_link_source` mints the `:Source` through `_mint_source_stub`, which
    # writes sourceKind/title/canonicalUrl/urlAliases/contentHash/ingestedAt
    # and NO `id`.
    sdk.link_source_to_entity(stub_url, widget["id"], "Object")
    proj = sdk._get_proj()
    assert proj.g.query(
        "MATCH (s:Source {url:$u}) RETURN s.id",
        params={"u": stub_url}).result_set[0][0] is None, (
        "the url-only stub now carries an `id` — re-derive this bound (the "
        "emission WOULD write a record)")

    with collect_identity_skips() as skips:
        assert sdk.create_edge("references", stub_url, widget["id"])["created"] is True
    live_by_url = proj.g.query(
        "MATCH (s:Source {url:$u})-[r:references]->(t {id:$t}) RETURN count(r)",
        params={"u": stub_url, "t": widget["id"]}).result_set[0][0]
    assert live_by_url == 1, (
        "the live write did not land — the rest of this test would be vacuous")
    assert not [
        e for e in _journal(events)
        if e.get("type") == "DirectEdgeCreated"
        and e.get("tgt") == widget["id"] and e.get("edge_type") == "references"
    ], "a record was emitted for a stub with no `id` — re-derive this bound"
    reported = [s for s in skips if "direct edge (SDK door)" in s.what]
    assert reported, (
        "the emission skipped an endpoint with no writable `id` in SILENCE — a "
        "live-only edge with no carrier and no signal is indistinguishable at "
        "runtime from the two DECLARED skips, and an operator cannot tell a "
        "deliberate degrade from data loss")
    assert all(s.drops_record for s in reported), (
        "a skipped endpoint means NO record for an edge that IS in the live "
        "graph, so the skip must be counted as a dropped RECORD, not as a "
        "lost derived edge")

    proj.rebuild_all(str(events), confirm_destructive=True)
    assert proj.g.query(
        "MATCH (s:Source {url:$u})-[r:references]->(t {id:$t}) RETURN count(r)",
        params={"u": stub_url, "t": widget["id"]}).result_set[0][0] == 0, (
        "a url-only `:Source` stub's edge now survives a rebuild — the bound in "
        "projection/edges.py and record_projection.py must be closed with it")


def test_bound_the_node_property_lane_emits_nothing(env):
    """BOUND: `sdk.create_object(..., ownedBy=…)` calls `proj.create_owned_by`
    DIRECTLY, so it never reaches the SDK door's emission — the commonest
    `ownedBy` producer is still live-only."""
    sdk, events = env
    team = sdk.create_subject("Team Atlas")
    widget = sdk.create_object("Widget Owned", "product", ownedBy=team["id"])

    proj = sdk._get_proj()
    live = _edge_count(proj, "ownedBy", widget["id"], team["id"])
    assert live == 1, "the node-property write did not land — vacuous otherwise"

    proj.rebuild_all(str(events), confirm_destructive=True)
    rebuilt = _edge_count(proj, "ownedBy", widget["id"], team["id"])
    assert rebuilt == 0, (
        "the node-property lane now carries its edge — route the claim in "
        "projection/edges.py to it (the wrappers are `proj.create_owned_by` "
        "calls, not the SDK door) and update this bound")


def test_bound_a_self_edge_is_minted_live_and_never_replayed(env):
    """BOUND: `create_edge(rel, X, X)` mints a live self-edge; the fold refuses
    to re-create one (`E2E-11.6` no-self contract), so no carrier is emitted."""
    sdk, events = env
    widget = sdk.create_object("Widget Self", "product")

    assert sdk.create_edge("related", widget["id"], widget["id"])["created"] is True
    proj = sdk._get_proj()
    assert _edge_count(proj, "related", widget["id"], widget["id"]) == 1
    assert not [
        e for e in _journal(events)
        if e.get("type") == "DirectEdgeCreated"
        and e.get("src") == widget["id"] and e.get("tgt") == widget["id"]
    ], "a self-edge carrier was emitted — the fold will drop it at replay"

    proj.rebuild_all(str(events), confirm_destructive=True)
    assert _edge_count(proj, "related", widget["id"], widget["id"]) == 0, (
        "the fold now re-creates self-edges — the E2E-11.6 no-self contract "
        "changed, so this bound (and the emission's skip) must be re-derived")


def test_bound_an_edge_at_a_superseded_endpoint_is_not_restored(env):
    """BOUND: the structural predicates are not in `SUPERSEDE_STRUCTURAL_RELS`,
    so `supersede_point` journals no `DirectEdgeRepoint`; the fold then drops the
    creation (the endpoint is in `succ`). Live keeps the edge at the dead
    endpoint, replay loses it."""
    sdk, events = env
    p1 = sdk.create_point("statement", "edge at a doomed endpoint A")
    p2 = sdk.create_point("statement", "endpoint B")
    p3 = sdk.create_point("statement", "successor of A")
    assert sdk.create_edge("dependsOn", p1["id"], p2["id"])["created"] is True

    proj = sdk._get_proj()
    sdk.supersede_point(p1["id"], p3["id"])
    assert _edge_count(proj, "dependsOn", p1["id"], p2["id"]) == 1, (
        "the live supersede moved the edge — this bound no longer describes "
        "the behaviour")

    proj.rebuild_all(str(events), confirm_destructive=True)
    assert _edge_count(proj, "dependsOn", p1["id"], p2["id"]) == 0, (
        "an edge at a superseded endpoint now survives a rebuild — the bound in "
        "projection/edges.py and record_projection.py must be closed with it")
