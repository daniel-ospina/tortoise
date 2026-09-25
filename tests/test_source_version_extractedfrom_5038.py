"""#5256 (extracted from #5038) — the ``extractedFrom`` READ-VERSION anchor.

A ``Point`` created against a ``:Source`` with a non-empty ``contentHash`` must
record **the version it was read from** as ``sourceVersion`` on its
``extractedFrom`` link, durably enough to survive a full ``rebuild_all``.

Design (settled in the scoping plan
``docs/plans/2026-09-25-5256-extractedfrom-source-version.md``):

* The **edge** is authoritative — ``r.sourceVersion`` is the per-link scalar
  (mirrors #5199's derivation anchor).
* The value is carried into the Point's **own journaled snapshot** as a declared
  ``sourceVersionTransit`` node property: a list of
  ``[resolved_source_key, hash]`` pairs. ``extractedFrom`` is a replay-derived
  projection (pass 2 wipes and re-creates the edges), so the value must travel
  WITH the point or it is lost. It is named ``...Transit`` (not
  ``sourceVersion``) so a Point read never shadows the canonical edge scalar
  (ONTOLOGY §4.6) with a value of a different type.
* ``_link_source`` **never** reads ``s.contentHash`` at replay time: the
  Source's in-place hash bump is unjournalled (#5024), so a re-read can record
  a version the Point was never read from — a FALSE current.
* Absent is honest-absent: no Source, or a Source with ``''``/no hash ⇒ no
  property at all (never ``''`` — it compares equal to a Source's ``''``).

Every test that pins a guard names the mutation that makes it FAIL.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.api import EventAPI
from tortoise.log import EventLog
from tortoise.projection import InMemoryProjection
from tortoise.sdk import TortoiseSDK

DOC = "https://doc.example/a"
DOC2 = "https://doc.example/b"


@pytest.fixture
def prov(tmp_path):
    """(sdk, events_dir, log_path) — one shared JSONL journal for SDK + EventAPI."""
    db = os.path.join(str(tmp_path), "sv.db")
    events = tmp_path / "events"
    events.mkdir()
    log_path = events / "events.jsonl"
    sdk = TortoiseSDK(db, event_log_path=str(log_path))
    yield sdk, events, log_path
    sdk.close()


def _proj(sdk):
    return sdk._get_proj()


def _edge_version(proj, pid: str, url: str):
    """The ``r.sourceVersion`` on the (point, source) extractedFrom edge."""
    rows = proj.g.query(
        "MATCH (p:Point {id:$id})-[r:extractedFrom]->(s:Source {url:$url}) "
        "RETURN r.sourceVersion",
        params={"id": pid, "url": url}).result_set
    return rows[0][0] if rows else "NO_EDGE"


def _has_edge(proj, pid: str, url: str) -> bool:
    return _edge_version(proj, pid, url) != "NO_EDGE"


def _node_transit(proj, pid: str):
    """The node's ``sourceVersionTransit`` carrier, or the sentinel ``"ABSENT"``."""
    rows = proj.g.query(
        "MATCH (p:Point {id:$id}) RETURN p.sourceVersionTransit",
        params={"id": pid}).result_set
    if not rows:
        return "NO_NODE"
    return "ABSENT" if rows[0][0] is None else rows[0][0]


def _journal_point(log_path, pid: str) -> dict:
    """The LAST ``PointAdded`` payload for ``pid`` from the JSONL journal."""
    point = None
    for line in Path(log_path).read_text().splitlines():
        if not line.strip():
            continue
        ev = json.loads(line)
        if ev.get("type") == "PointAdded" and ev.get("point", {}).get("id") == pid:
            point = ev["point"]
    assert point is not None, f"no PointAdded for {pid} in the journal"
    return point


def _rebuilt(sdk, events):
    """Wipe + replay into the same graph handle; return the fresh read surface."""
    sdk._get_proj().rebuild_all(str(events))
    return sdk._get_proj()


# ── the stamp: edge + transit agree, and survive a rebuild ─────────────────


def test_scalar_ref_records_the_version_on_the_edge_and_the_transit(prov):
    """Input: Source(url=DOC, contentHash='h1'); Point with a SCALAR ref.

    FAILS IF the edge carries no ``sourceVersion``, OR the node carrier is
    missing/not ``[[resolved_url,'h1']]``. The scalar form is the ACCEPTANCE
    case and the one a list-only resolver would iterate character-wise.
    """
    sdk, _events, _log = prov
    sdk.create_source(DOC, "document", contentHash="h1")
    p = sdk.create_point("statement", "claim", extractedFrom=DOC)

    proj = _proj(sdk)
    key = proj.g.query(
        "MATCH (s:Source {url:$url}) RETURN s.url",
        params={"url": DOC}).result_set[0][0]
    assert _edge_version(proj, p["id"], key) == "h1"
    assert _node_transit(proj, p["id"]) == [[key, "h1"]]


def test_rebuild_preserves_the_anchor_byte_for_byte(prov):
    """Acceptance 1: live == rebuild for BOTH the edge scalar and the transit.

    FAILS IF ``rebuild_all`` loses the transit (the property is not journaled),
    leaves the edge bare (pass 2 does not re-stamp), or the replay re-reads the
    Source and disagrees with the live value.
    """
    sdk, events, _log = prov
    sdk.create_source(DOC, "document", contentHash="h1")
    p = sdk.create_point("statement", "claim", extractedFrom=DOC)

    proj = _proj(sdk)
    key = proj.g.query(
        "MATCH (s:Source {url:$url}) RETURN s.url",
        params={"url": DOC}).result_set[0][0]
    live = (_edge_version(proj, p["id"], key), _node_transit(proj, p["id"]))
    assert live == ("h1", [[key, "h1"]]), f"guard: live anchor wrong: {live!r}"

    proj = _rebuilt(sdk, events)
    assert (_edge_version(proj, p["id"], key), _node_transit(proj, p["id"])) == live


def test_source_revision_after_the_read_does_not_change_the_anchor(prov):
    """Input: read at 'h1', then revise the Source to 'h2', then rebuild.

    FAILS IF the replay reads ``s.contentHash`` — the anchor would jump to 'h2'
    and the Point would claim it was read from a version it never saw (the
    false-current this design exists to prevent). The revision is JOURNALED
    (``create_source`` emits on every write), so the Source really is 'h2' after
    the rebuild; only the Point's own snapshot may decide the anchor.
    """
    sdk, events, _log = prov
    sdk.create_source(DOC, "document", contentHash="h1")
    p = sdk.create_point("statement", "claim", extractedFrom=DOC)

    sdk.create_source(DOC, "document", contentHash="h2")  # journaled revision

    proj = _rebuilt(sdk, events)
    key = proj.g.query(
        "MATCH (s:Source {url:$url}) RETURN s.url",
        params={"url": DOC}).result_set[0][0]
    assert proj.g.query(
        "MATCH (s:Source {url:$url}) RETURN s.contentHash",
        params={"url": DOC}).result_set[0][0] == "h2", \
        "guard: the rebuild must have restored the revised Source"
    assert _edge_version(proj, p["id"], key) == "h1", \
        "the replay re-read the Source — a false current"
    assert _node_transit(proj, p["id"]) == [[key, "h1"]]


def test_list_refs_survive_rebuild_with_their_versions(prov):
    """The per-link anchor must be reproducible from the journal too.

    FAILS IF the replay unions/squashes the pairs (each edge would then get the
    same hash) or if only the node transit is restored.
    """
    sdk, events, _log = prov
    sdk.create_source(DOC, "document", contentHash="h1")
    sdk.create_source(DOC2, "document", contentHash="h2")
    p = sdk.create_point("statement", "claim", extractedFrom=[DOC, DOC2])

    live = (_edge_version(_proj(sdk), p["id"], DOC),
            _edge_version(_proj(sdk), p["id"], DOC2))
    assert live == ("h1", "h2"), f"guard: live fan-out wrong: {live!r}"
    proj = _rebuilt(sdk, events)
    assert (_edge_version(proj, p["id"], DOC),
            _edge_version(proj, p["id"], DOC2)) == live


def test_url_variant_ref_uses_the_resolved_key(prov):
    """Input: a Source stored canonically, linked via a URL VARIANT.

    FAILS IF ``resolve_source_versions`` keys by the RAW ref (or by
    ``normalize_source_url``) instead of ``resolve_source_key``'s return — the
    variant key would not match the node's stored ``url``, the lookup would
    miss, and the edge would land bare with no error (the key contract the
    resolver docstring calls load-bearing).
    """
    sdk, events, _log = prov
    sdk.create_source(DOC, "document", contentHash="h1")
    variant = "HTTPS://Doc.Example/a/?utm_source=x"
    p = sdk.create_point("statement", "variant claim", extractedFrom=variant)

    proj = _proj(sdk)
    assert _edge_version(proj, p["id"], DOC) == "h1"
    assert _node_transit(proj, p["id"]) == [[DOC, "h1"]], \
        "the transit must key on the resolved node url, not the raw variant"
    proj = _rebuilt(sdk, events)
    assert _edge_version(proj, p["id"], DOC) == "h1"
    assert _node_transit(proj, p["id"]) == [[DOC, "h1"]]


def test_dedup_recommit_does_not_raise_or_diverge(prov):
    """Input: ``create_or_update_point(..., extractedFrom=DOC)`` twice.

    FAILS IF the new resolver/reject runs BEFORE the dedup early-return — an
    idempotent re-commit would then raise (or re-anchor) where it must be a
    no-op. Pins the anchor is unchanged and the graph stays faithful to the
    journal.
    """
    from tortoise.consistency import check_consistency

    sdk, _events, log_path = prov
    sdk.create_source(DOC, "document", contentHash="h1")
    first = sdk.create_or_update_point("statement", "idem", extractedFrom=DOC)
    second = sdk.create_or_update_point("statement", "idem", extractedFrom=DOC)
    assert second["id"] == first["id"], "guard: this must be a dedup hit"
    assert _edge_version(_proj(sdk), first["id"], DOC) == "h1"
    assert check_consistency(str(log_path), _proj(sdk))["ok"]


# ── honest absent ──────────────────────────────────────────────────────────


def test_source_with_no_hash_gets_no_anchor(prov):
    """Input: a registered Source with ``contentHash=''``.

    FAILS IF ``''`` is written as the anchor — it compares EQUAL to the
    Source's ``''`` and reads as a false CURRENT (the #5199 rule).
    """
    sdk, _events, _log = prov
    sdk.create_source(DOC, "document", contentHash="")
    p = sdk.create_point("statement", "claim", extractedFrom=DOC)

    proj = _proj(sdk)
    assert _has_edge(proj, p["id"], DOC), "guard: the edge itself must exist"
    assert _edge_version(proj, p["id"], DOC) is None
    assert _node_transit(proj, p["id"]) == "ABSENT"


def test_unregistered_source_gets_no_anchor(prov):
    """Input: a ref with no ``:Source`` node at all (the stub is minted with
    ``contentHash=''``).

    FAILS IF a caller ref alone produces an anchor, or if the transit is written
    as ``[]``/``''`` rather than omitted.
    """
    sdk, _events, _log = prov
    p = sdk.create_point("statement", "claim", extractedFrom=DOC)

    proj = _proj(sdk)
    assert _has_edge(proj, p["id"], DOC)
    assert _edge_version(proj, p["id"], DOC) is None
    assert _node_transit(proj, p["id"]) == "ABSENT"


def test_journal_payload_omits_the_key_when_there_is_no_anchor(prov):
    """Input: the un-hashed case above.

    FAILS IF the payload carries ``sourceVersionTransit: null`` — presence is ownership
    in this journal, and an explicit null would make the replay treat a field
    the writer never recorded as journal-owned.
    """
    sdk, _events, log = prov
    p = sdk.create_point("statement", "claim", extractedFrom=DOC)
    assert "sourceVersionTransit" not in _journal_point(log, p["id"])


def test_transit_is_a_pair_list_for_a_hash_bearing_source(prov):
    """Scoped transit⇔edge invariant: for a Point created through
    ``create_point`` against a hash-bearing Source, the transit exists and is a
    list of 2-element ``[str, str]`` pairs — never a scalar, and its hash agrees
    with the edge's scalar.

    FAILS IF the CREATE-map write is lost (no transit at all — M14), if an
    empty transit is written instead of omitted (M3), or if a pair degenerates
    to a scalar/list-of-scalars.

    (The honest-absent case above is the deliberate exception: an edge with NO
    transit. ``EventAPI``'s ingest-connection leg is out of scope here.)
    """
    sdk, _events, _log = prov
    sdk.create_source(DOC, "document", contentHash="h1")
    p = sdk.create_point("statement", "claim", extractedFrom=DOC)

    proj = _proj(sdk)
    transit = _node_transit(proj, p["id"])
    assert _has_edge(proj, p["id"], DOC)
    assert isinstance(transit, list) and transit, transit
    assert all(
        isinstance(pair, list) and len(pair) == 2 and isinstance(pair[0], str)
        and isinstance(pair[1], str)
        for pair in transit
    ), transit
    # Every pair must agree with an edge — not just the first.
    for key, h in transit:
        assert _edge_version(proj, p["id"], key) == h, (key, h)


# ── the ON CREATE guard (a re-link must not advance the record) ─────────────


def test_relink_does_not_advance_the_recorded_version(prov):
    """Input: anchor at 'h1', then re-link the SAME (point, source) with 'h2'.

    FAILS IF ``_link_source`` uses ``SET`` instead of ``ON CREATE SET`` — the
    recorded version would advance and the staleness the anchor exists to
    expose would read as CURRENT (the #5199 rule).
    """
    sdk, _events, _log = prov
    sdk.create_source(DOC, "document", contentHash="h1")
    p = sdk.create_point("statement", "claim", extractedFrom=DOC)

    _proj(sdk)._link_source(p["id"], DOC, source_versions={DOC: "h2"})
    assert _edge_version(_proj(sdk), p["id"], DOC) == "h1"


# ── the #4042 recreate wipe (node-prop half) ───────────────────────────────


def test_recreated_point_does_not_inherit_the_node_transit(prov):
    """Input: a sourced Point hard-deleted, then re-created with the SAME id
    and NO ``extractedFrom``, then rebuilt.

    FAILS IF the #4042 recreate wipe does not clear ``n.sourceVersionTransit``:
    the pre-delete incarnation's carrier would survive the node MERGE (its
    clause only fires when the new payload carries one) and the rebuilt node
    would hold a property the live node does not (the #330/#5004 parity break).

    SCOPE: asserts the NODE only. The old incarnation's ``extractedFrom`` EDGE
    is separately resurrected by pass 2 today — a PRE-EXISTING gap in
    ``extractedFrom`` itself (the recreate wipe never touches edges), recorded
    as a residual of this change and not introduced here.
    """
    sdk, events, _log = prov
    sdk.create_source(DOC, "document", contentHash="h1")
    p = sdk.create_point("statement", "first life", extractedFrom=DOC)
    pid = p["id"]
    # A JOURNALED hard delete (``EntityMutated op=delete``) is what makes the
    # next same-id creation a RE-creation to pass 1a (`pending_deleted`) — a raw
    # DETACH DELETE has no journal record, so the wipe would never fire and the
    # test would pass for the wrong reason.
    assert sdk.delete_point(pid) is True

    sdk.create_point("statement", "second life", id=pid)
    assert _node_transit(_proj(sdk), pid) == "ABSENT", \
        "guard: the live re-creation must carry no transit"

    proj = _rebuilt(sdk, events)
    assert _node_transit(proj, pid) == "ABSENT", \
        "the dead incarnation's sourceVersionTransit survived the recreate wipe"


# ── fail-closed rejects on every tenant write surface ──────────────────────


def test_sdk_create_point_rejects_a_forged_source_version(prov):
    """FAILS IF a tenant can forge the provenance record via ``create_point``."""
    sdk, _events, _log = prov
    for key in ("sourceVersion", "sourceVersions", "sourceVersionTransit"):
        with pytest.raises(ValueError, match="server-managed"):
            sdk.create_point("statement", "forged", **{key: "h9"})


def test_sdk_update_point_rejects_a_forged_source_version(prov):
    """``update_point`` shares the ``_sanitize_props`` backstop.

    FAILS IF the reject lives only on the create path. All three names.
    """
    sdk, _events, _log = prov
    p = sdk.create_point("statement", "claim")
    for key in ("sourceVersion", "sourceVersions", "sourceVersionTransit"):
        with pytest.raises(ValueError, match="server-managed"):
            sdk.update_point(p["id"], **{key: "h9"})


def test_sdk_create_document_rejects_a_forged_source_version(prov):
    """Indicator 3 names ``create_document`` as a tenant surface.

    FAILS IF the Document route (``create_document`` → ``_create_entity`` →
    ``_sanitize_props``) is refactored to skip the sanitizer, or the
    ``extractedFrom`` link runs before it. All three names; nothing written.
    """
    sdk, _events, _log = prov
    for key in ("sourceVersion", "sourceVersions", "sourceVersionTransit"):
        with pytest.raises(ValueError, match="server-managed"):
            sdk.create_document("T", "report", **{key: "h9"})
    assert _proj(sdk).g.query(
        "MATCH (d:Document) RETURN count(d)").result_set[0][0] == 0


def test_ingest_bundle_rejects_a_forged_source_version(prov):
    """The Phase-1 shape check must reject it, for all three names, with NO
    section committed.

    FAILS IF a bundle item can splat-bind the kwarg / carry the prop, OR the
    reject no longer precedes the writes. The match pins the PHASE-1 message
    ("on bundle items") on purpose: the ``_sanitize_props`` backstop would also
    raise, but with a different message — so a test matching only
    "server-managed" would stay green if the shape check were removed, and the
    Phase-1-before-any-write guarantee would be unpinned. The valid ``sources``
    item ahead of the forged point is what makes a Phase-2-only path visibly
    commit a Source.
    """
    sdk, _events, _log = prov
    for key in ("sourceVersion", "sourceVersions", "sourceVersionTransit"):
        with pytest.raises(ValueError, match="on bundle items"):
            sdk.ingest({
                "sources": [{"url": "https://s.example/x",
                             "sourceKind": "document"}],
                "points": [{"kind": "claim", "content": "x", key: "h9"}],
            })
        sources = _proj(sdk).g.query(
            "MATCH (s:Source) RETURN count(s)").result_set[0][0]
        assert sources == 0, (
            f"a forged {key} must abort before the sources section commits")
        points = _proj(sdk).g.query(
            "MATCH (p:Point) RETURN count(p)").result_set[0][0]
        assert points == 0


def test_mcp_boundary_rejects_a_forged_source_version():
    """The tenant MCP choke point rejects all three names.

    FAILS IF the MCP boundary is weaker than the SDK backstop.
    """
    from tortoise.mcp_server import _reject_server_managed_props
    for key in ("sourceVersion", "sourceVersions", "sourceVersionTransit"):
        assert _reject_server_managed_props({key: "h9"}) is not None
    assert _reject_server_managed_props({"content": "ok"}) is None


# ── the second producer: EventAPI (the extractor lane) ─────────────────────


def test_eventapi_add_point_anchors_the_version(prov):
    """The extractor lane journals through ``EventAPI``, not ``create_point``.

    FAILS IF only the SDK producer anchors — an unanchored provenance edge on
    the ingest lane is the exact class #5038 is about. Also pins that the
    anchor survives a rebuild of that lane's record.
    """
    sdk, events, log_path = prov
    sdk.create_source(DOC, "document", contentHash="h1")
    api = EventAPI(EventLog(log_path), initiated_by="extractor",
                   projection=_proj(sdk))
    pid = api.add_point("claim from a doc",
                        {"source_id": DOC}, extractedFrom=DOC)

    proj = _proj(sdk)
    assert _edge_version(proj, pid, DOC) == "h1"
    assert _node_transit(proj, pid) == [[DOC, "h1"]]

    proj = _rebuilt(sdk, events)
    assert _edge_version(proj, pid, DOC) == "h1"
    assert _node_transit(proj, pid) == [[DOC, "h1"]]


def test_eventapi_add_point_rejects_a_forged_source_version(prov):
    """``EventAPI`` does not pass through ``_sanitize_props`` — the reject is
    local to it. All three names (the plural is in the set but on a DIFFERENT
    key, so dropping one would leave the others green).

    FAILS IF this non-SDK journal producer lets a caller forge the anchor.
    """
    sdk, _events, log_path = prov
    api = EventAPI(EventLog(log_path), initiated_by="extractor",
                   projection=_proj(sdk))
    for key in ("sourceVersion", "sourceVersions", "sourceVersionTransit"):
        with pytest.raises(ValueError, match="sourceVersion"):
            api.add_point("forged", {}, **{key: "h9"})


def test_eventapi_add_point_without_a_graph_does_not_crash(prov):
    """A lane with no usable graph handle must simply carry no anchor.

    FAILS IF the anchor code dereferences ``self.projection.g`` unguarded — the
    write would crash. Covers BOTH no-projection and the in-memory double
    (which has no ``.g``), because a guard narrowed to ``if self.projection is
    not None`` would pass the first leg and crash the second.
    """
    sdk, _events, log_path = prov  # noqa: RUF059 — the projection stays unset
    endpoints = [
        EventAPI(EventLog(log_path), initiated_by="extractor"),  # None
        EventAPI(EventLog(log_path), initiated_by="extractor",
                 projection=InMemoryProjection()),               # no .g
    ]
    for api in endpoints:
        assert api.add_point("loose claim", {}, extractedFrom=DOC)


def test_create_source_rejects_a_forged_source_version(prov):
    """``create_source`` BYPASSES ``_sanitize_props`` (``_skip_sanitize=True``)
    and relies on its own reject list.

    FAILS IF the version keys are absent from that list — a caller-supplied
    value would persist on the Source node (the security review's reproduced
    hole). All three spellings.
    """
    sdk, _events, _log = prov
    for key in ("sourceVersion", "sourceVersions", "sourceVersionTransit"):
        with pytest.raises(ValueError, match="server-managed provenance"):
            sdk.create_source(DOC, "document", **{key: "h9"})
    assert _proj(sdk).g.query(
        "MATCH (s:Source) RETURN count(s)").result_set[0][0] == 0


# ── the supersede boundary (R1: the transfer carries nothing) ──────────────


def test_supersede_transfer_carries_no_anchor(prov):
    """Plan §6-17: the version transfer must stay VERSION-FREE (R1, the half
    deferred to #5038).

    FAILS IF a future change copies edge properties on transfer
    (``SET r2 += properties(r)``) or hands ``source_versions`` to the transfer
    link — the successor would then claim a read it never made.
    """
    sdk, events, _log = prov
    sdk.create_source(DOC, "document", contentHash="h1")
    old = sdk.create_point("statement", "predecessor", extractedFrom=DOC)
    new = sdk.create_point("statement", "successor")
    sdk.supersede_point(old["id"], new["id"])

    proj = _proj(sdk)
    assert _edge_version(proj, new["id"], DOC) is None, \
        "the transferred edge must carry no version"
    assert _node_transit(proj, new["id"]) == "ABSENT"
    assert _edge_version(proj, old["id"], DOC) == "NO_EDGE"
    assert _node_transit(proj, old["id"]) == [[DOC, "h1"]]

    proj = _rebuilt(sdk, events)
    assert _edge_version(proj, new["id"], DOC) is None
    assert _node_transit(proj, new["id"]) == "ABSENT"


# ── malformed-journal resilience ───────────────────────────────────────────


def test_malformed_carrier_payload_contributes_no_anchor_and_no_crash(prov):
    """A corrupt/foreign journal line must contribute NO anchor on EITHER writer,
    and must not abort ``rebuild_all`` midway.

    FAILS IF ``_upsert_point_props``/``_upsert_point_edges`` trust the payload: a
    non-persistable value (a dict, or a list containing one) raises a Falkor
    ``ResponseError`` mid-rebuild and leaves the graph WIPED — the recovery path
    aborting on the very corruption it exists to repair.

    The ``bad-edge`` case pins the SHARED all-or-nothing predicate: with a
    per-pair filter on the edge side, the one valid pair would stamp
    ``r.sourceVersion`` while the node clause wrote no carrier — an edge anchor
    with no gate-compared record. It must produce NEITHER.

    The ``bad-empty-hash``/``bad-blank-hash`` cases pin the non-empty member
    rule: an empty/blank hash is the producers' HONEST-ABSENT signal, so a
    hand-written ``[[DOC, '']]`` must not leave the node carrier claiming a
    pair for DOC while ``_anchor_on_create`` nulls the edge — and a blank
    ``'   '`` must not stamp garbage on the authoritative edge (the CASE only
    matches ``''``). Both writers must agree it is absent.
    """
    sdk, events, log_path = prov
    sdk.create_point("statement", "the good point")
    entries = [
        ("bad-dict", {"sourceVersionTransit": {"not": "persistable"}}),
        ("bad-list", {"sourceVersionTransit": [["a", "h"], {"x": 1}]}),
        ("bad-scalar", {"sourceVersionTransit": "loose"}),
        ("bad-short-pair", {"sourceVersionTransit": [["only-one-element"]]}),
        ("bad-numeric", {"sourceVersionTransit": [[1, 2]]}),
        ("bad-empty-hash", {"extractedFrom": DOC,
                            "sourceVersionTransit": [[DOC, ""]]}),
        ("bad-blank-hash", {"extractedFrom": DOC,
                            "sourceVersionTransit": [[DOC, "   "]]}),
        ("bad-edge", {"extractedFrom": DOC,
                      "sourceVersionTransit": [[DOC, "h9"], {"x": 1}]}),
    ]
    with open(log_path, "a", encoding="utf-8") as fh:
        for pid, extra in entries:
            fh.write(json.dumps({
                "type": "PointAdded",
                "point": {"id": pid, "content": "bad", "kind": "statement",
                          "status": "live", **extra},
            }) + "\n")

    sdk._get_proj().rebuild_all(str(events))  # must NOT raise
    proj = _proj(sdk)
    for pid, extra in entries:
        assert _node_transit(proj, pid) == "ABSENT", pid
        if "extractedFrom" in extra:
            assert _has_edge(proj, pid, DOC), f"{pid}: guard — the edge must exist"
            assert _edge_version(proj, pid, DOC) is None, \
                f"{pid}: a partially-malformed carrier must not stamp the edge"


# ── the gate sees it and stays green on a faithful graph ───────────────────


def test_consistency_gate_compares_the_transit(prov):
    """The #5011 content gate must COMPARE ``sourceVersionTransit``, not merely
    tolerate it.

    FAILS IF the carrier was added to an exclusion list (the positive half), OR
    — the discriminating half — if a tampered graph still reads healthy. That
    second case is exactly what happens when ``sourceVersionTransit`` is dropped
    from ``_POINT_HANDLED``: it falls into ``_uncarried`` and is ``skip``-ped
    from BOTH sides, so a raw-Cypher forgery goes unseen. Hence the explicit
    ``uncarried_journal_fields`` assertion.

    The graph is seeded through the **replay writer** (``rebuild_all``), NOT the
    ``create_point`` CREATE-map write, so the positive half also pins the
    ``_upsert_point_props`` carrier clause: remove that clause and the faithful
    rebuild carries nothing, the positive ``ok`` assertion fails.
    """
    from tortoise.consistency import check_consistency

    sdk, events, log_path = prov
    sdk.create_source(DOC, "document", contentHash="h1")
    p = sdk.create_point("statement", "claim", extractedFrom=DOC)
    sdk._get_proj().rebuild_all(str(events))

    result = check_consistency(str(log_path), _proj(sdk))
    assert result["ok"], result
    assert result["divergence"] is None, result
    assert "sourceVersionTransit" not in result.get("excluded_fields", {}), \
        "the carrier must NOT be excluded from the content comparison"
    assert "sourceVersionTransit" not in result.get(
        "uncarried_journal_fields", []), \
        "the carrier must be CARRIED by the replay, not skipped as uncarried"

    # Discriminating half: forge the carrier in place and require the gate to
    # name the field. (An unjournalled graph edit ⇒ unrecorded-mutation.)
    _proj(sdk).g.query(
        "MATCH (n:Point {id:$id}) SET n.sourceVersionTransit=[['forged','h9']]",
        params={"id": p["id"]})
    tampered = check_consistency(str(log_path), _proj(sdk))
    assert tampered["ok"] is False, tampered
    named = {
        field
        for dp in tampered.get("divergent_points") or []
        for field in (dp.get("fields") or [])
    }
    assert "sourceVersionTransit" in named, tampered
    assert "sourceVersionTransit" not in tampered.get("excluded_fields", {}), \
        tampered
    assert "sourceVersionTransit" not in tampered.get(
        "uncarried_journal_fields", []), tampered
