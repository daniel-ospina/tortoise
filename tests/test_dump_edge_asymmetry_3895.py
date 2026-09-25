"""#3895 — the logical dump's two halves must be exported over ONE node set.

The first production restore drill (2026-09-17) restored a real 21.6 MB /
9997-node / 10000-edge artifact and was REJECTED:

    Edge restore incomplete: 9687/10000 linked — dump references missing nodes

Root cause: ``dump_graph`` filtered the NODE list by ``_is_export_skip_node``
(#1625) and exported EVERY edge. An edge whose endpoint the node loop skipped
loses its ``MATCH (a {__dump_id:$s}), (b {__dump_id:$d})`` on restore, is
silently dropped, and the integrity gate refuses the whole artifact — so every
already-written artifact of that shape is unrestorable by construction.

These tests assert the OBSERVABLE (restored node/edge counts, the presence of a
specific real edge, the reported drop counts) — never a source string.

RED proof (the mutation that must fail): with the pre-fix writer the round-trip
test's dump carries marker-incident edges whose endpoints it does not carry, so
``restore_graph`` raises ``Edge restore incomplete``.
"""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.hosted_api import _is_export_skip_node
from tortoise.hosted_backup import (
    DUMP_FORMAT,
    MemoryStorage,
    create_backup,
    dump_graph,
    restore_graph,
)
from tortoise.projection import FalkorProjection


def _make_proj(tmpdir: str, name: str = "t.db") -> FalkorProjection:
    return FalkorProjection(os.path.join(tmpdir, name))


def _seed_points_and_marker_edges(g) -> None:
    """A real user edge (Point→Point) PLUS edges incident to export-skipped
    bookkeeping markers — the shape that made the production artifact
    unrestorable (a marker node no writer addresses, carrying an edge)."""
    g.query("CREATE (p:Point {id:'p1', content:'one', pointKind:'claim'})")
    g.query("CREATE (p:Point {id:'p2', content:'two', pointKind:'claim'})")
    g.query(
        "MATCH (a:Point {id:'p1'}), (b:Point {id:'p2'}) "
        "CREATE (a)-[:IMPL {weight:0.9}]->(b)"
    )
    # Export-skipped bookkeeping, each carrying an edge (the defect's input).
    g.query("MERGE (m:GraphEventMeta) SET m.last_seq = 7, m.first_seq = 1")
    g.query("MERGE (m:EpMeta) SET m.ep_version = 3")
    g.query("MERGE (m:Meta {key:'point_fts_v2'}) SET m.v = true")
    g.query(
        "MATCH (p:Point {id:'p1'}), (m:GraphEventMeta) "
        "CREATE (p)-[:STALE_BOOKKEEPING {n:1}]->(m)"
    )
    g.query(
        "MATCH (p:Point {id:'p2'}), (e:EpMeta) "
        "CREATE (p)-[:STALE_BOOKKEEPING {n:2}]->(e)"
    )
    g.query(
        "MATCH (p:Point {id:'p1'}), (m:Meta {key:'point_fts_v2'}) "
        "CREATE (p)-[:STALE_BOOKKEEPING {n:3}]->(m)"
    )


def _edge_uids(g) -> set[tuple[str, str, str]]:
    """Restored edges as (src id, type, dst id) over user-addressable props."""
    rows = g.query(
        "MATCH (a)-[r]->(b) RETURN "
        "coalesce(a.id, a.name, ''), type(r), coalesce(b.id, b.name, '')"
    ).result_set
    return {(str(s), str(t), str(d)) for s, t, d in rows}


# ── the fix: node set and edge set agree (#3895 AC1 + AC4) ──────────────────


def test_dump_edges_are_a_subset_of_the_dumped_node_set():
    """Every exported edge has BOTH endpoints in the exported node list — the
    invariant whose absence made the artifact unrestorable."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed_points_and_marker_edges(proj.g)
        dump = dump_graph(proj.g, graph_name="t")

        ids = {n["dump_id"] for n in dump["nodes"]}
        assert dump["nodes"], "seed must export nodes"
        assert all(e["src"] in ids and e["dst"] in ids for e in dump["edges"]), (
            "dump exports an edge whose endpoint it did not export — the "
            "artifact would be unrestorable (#3895)"
        )
        # Counts are over the SAME set, so the manifest can be trusted.
        assert dump["node_count"] == len(dump["nodes"])
        assert dump["edge_count"] == len(dump["edges"])
        # The markers are excluded, and the edges incident to them are
        # REPORTED, never silently dropped.
        assert dump["excluded_node_count"] == 3
        assert dump["skipped_edge_count"] == 3
        assert dump["unresolved_edge_count"] == 0
        proj.close()


def test_dump_restore_roundtrip_with_marker_incident_edges_is_complete():
    """AC4: a graph holding export-skipped markers WITH edges attached must
    round-trip completely, with restored counts matching the dump/manifest.

    Mutation (RED): revert the write-side filter -> the dump carries the three
    marker-incident edges but not the markers -> ``restore_graph`` raises
    ``Edge restore incomplete``.
    """
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp, "src.db")
        proj2 = _make_proj(tmp, "dst.db")
        _seed_points_and_marker_edges(proj.g)

        dump = dump_graph(proj.g, graph_name="tortoise")
        counts = restore_graph(proj2.g, dump)

        assert counts == {"nodes": dump["node_count"], "edges": dump["edge_count"]}
        assert counts["edges"] == 1, "only the real Point→Point edge is content"
        assert counts["nodes"] == 2
        proj.close()
        proj2.close()


# ── the negative control: a REAL edge must survive (mutation guard) ────────


def test_real_edge_survives_the_filter():
    """The edge the fix must NOT drop: a real user edge whose endpoints are
    normal Points. Mutation: make the filter too broad (drop every edge, or
    drop any edge touching a non-Point label) -> this fails."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp, "src.db")
        proj2 = _make_proj(tmp, "dst.db")
        _seed_points_and_marker_edges(proj.g)

        dump = dump_graph(proj.g, graph_name="tortoise")
        restore_graph(proj2.g, dump)

        # Observable: the specific real edge is present after restore.
        assert ("p1", "IMPL", "p2") in _edge_uids(proj2.g)
        assert dump["edge_count"] == 1
        assert len(_edge_uids(proj2.g)) == 1
        proj.close()
        proj2.close()


def test_real_edge_to_an_event_node_survives():
    """A second real edge class: an operator INPUT edge to a GraphEvent (the
    #1272 endpoint) — a Point→Event edge is content, not bookkeeping, and the
    marker filter must not touch it."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp, "src.db")
        proj2 = _make_proj(tmp, "dst.db")
        proj.g.query("CREATE (p:Point {id:'p1', content:'c', pointKind:'claim'})")
        proj.g.query(
            "CREATE (e:Event {id:'e1', eventId:'e1', subject:'s'})"
        )
        proj.g.query(
            "MATCH (p:Point {id:'p1'}), (e:Event {id:'e1'}) "
            "CREATE (p)-[:ABOUT {kind:'event'}]->(e)"
        )
        proj.g.query("MERGE (m:GraphEventMeta) SET m.last_seq = 1")

        dump = dump_graph(proj.g, graph_name="tortoise")
        counts = restore_graph(proj2.g, dump)

        assert counts["edges"] == 1
        assert ("p1", "ABOUT", "e1") in _edge_uids(proj2.g)
        proj.close()
        proj2.close()


# ── genuine corruption still fails closed (AC3) ────────────────────────────


def test_removed_non_skip_node_still_fails_closed():
    """AC3 mutation: remove a NON-skipped node from a dump's node list -> the
    restore must still raise. The gate must not become a blanket amnesty for
    dangling edges."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp, "dst.db")
        dump = {
            "format": DUMP_FORMAT,
            "nodes": [{"dump_id": 1, "labels": ["Point"], "props": {"id": "p1"}}],
            "edges": [{"src": 1, "dst": 99, "type": "IMPL", "props": {}}],
        }
        with pytest.raises(ValueError, match="Edge restore incomplete"):
            restore_graph(proj.g, dump)
        proj.close()


def test_removed_node_from_a_real_dump_still_fails_closed():
    """Same, end-to-end from a real ``dump_graph``: drop one exported node
    (a normal Point) and the restore refuses."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp, "src.db")
        proj2 = _make_proj(tmp, "dst.db")
        _seed_points_and_marker_edges(proj.g)
        dump = dump_graph(proj.g, graph_name="tortoise")
        # Drop the SECOND node (p2, a Point) — its edge's dst is now absent.
        dump["nodes"] = [n for n in dump["nodes"] if n["props"].get("id") != "p2"]

        with pytest.raises(ValueError, match="Edge restore incomplete"):
            restore_graph(proj2.g, dump)
        proj.close()
        proj2.close()


def test_skipped_endpoint_is_not_amnesty_for_genuine_corruption():
    """A dump may not claim dangling edges are benign export-skip drops: the
    removal of a real node raises even when the dump also contains a
    marker-incident edge and the caller did NOT opt into salvage."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp, "dst.db")
        dump = {
            "format": DUMP_FORMAT,
            "nodes": [
                {"dump_id": 1, "labels": ["Point"], "props": {"id": "p1"}},
                {"dump_id": 2, "labels": ["Meta"], "props": {"key": "point_fts_v2"}},
            ],
            "edges": [
                {"src": 1, "dst": 2, "type": "BOOKKEEPING", "props": {}},
                {"src": 1, "dst": 77, "type": "IMPL", "props": {}},
            ],
        }
        with pytest.raises(ValueError, match="Edge restore incomplete"):
            restore_graph(proj.g, dump)
        proj.close()


# ── the legacy-artifact policy, explicitly and never silently (AC2) ────────


def test_legacy_dump_refusal_names_the_count_and_the_skip_classes():
    """AC2 policy: by default a pre-fix artifact (dangling edge) is REFUSED,
    with the exact count and the export-skip classes named — the bare
    'dump references missing nodes' is what made the production failure
    undiagnosable."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp, "dst.db")
        dump = {
            "format": DUMP_FORMAT,
            "nodes": [{"dump_id": 1, "labels": ["Point"], "props": {"id": "p1"}}],
            "edges": [
                {"src": 1, "dst": 1, "type": "IMPL", "props": {}},
                {"src": 1, "dst": 2, "type": "STALE_BOOKKEEPING", "props": {}},
            ],
        }
        with pytest.raises(ValueError) as ei:
            restore_graph(proj.g, dump)
        msg = str(ei.value)
        assert "Edge restore incomplete" in msg
        assert "1/2" in msg  # exact count of linked edges
        assert "GraphEventMeta" in msg  # the omitted node classes, named
        assert "allow_dangling_edges=True" in msg  # the actionable repair path
        proj.close()


def test_legacy_dump_salvage_restores_linkable_edges_and_reports_drops():
    """AC2 repair-on-read: an already-written artifact CAN be restored — the
    linkable edges are restored, the unlinkable ones are DROPPED but COUNTED
    and their endpoint ids returned. Never a silent drop."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp, "dst.db")
        dump = {
            "format": DUMP_FORMAT,
            "nodes": [
                {"dump_id": 1, "labels": ["Point"], "props": {"id": "p1"}},
                {"dump_id": 3, "labels": ["Point"], "props": {"id": "p3"}},
            ],
            "edges": [
                {"src": 1, "dst": 3, "type": "IMPL", "props": {"weight": 1.0}},
                {"src": 1, "dst": 2, "type": "STALE_BOOKKEEPING", "props": {}},
            ],
        }
        counts = restore_graph(proj.g, dump, allow_dangling_edges=True)

        assert counts["nodes"] == 2
        assert counts["edges"] == 1
        assert counts["dropped_edges"] == 1
        assert counts["dropped_edge_endpoints"] == [2]
        # The real edge is there — the salvage path drops only the unlinkable.
        assert ("p1", "IMPL", "p3") in _edge_uids(proj.g)
        proj.close()


def test_salvage_is_opt_in_and_default_still_refuses():
    """The salvage path must never be the default (a silent-drop road)."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp, "dst.db")
        dump = {
            "format": DUMP_FORMAT,
            "nodes": [{"dump_id": 1, "labels": ["Point"], "props": {"id": "p1"}}],
            "edges": [{"src": 1, "dst": 2, "type": "X", "props": {}}],
        }
        with pytest.raises(ValueError, match="Edge restore incomplete"):
            restore_graph(proj.g, dump)
        proj.close()


# ── the manifest counts are trustworthy (second half of #3895) ─────────────


def test_manifest_counts_are_over_the_same_node_set(monkeypatch):
    """AC: ``node_count``/``edge_count`` describe one node set, and the
    exclusions are auditable from the PLAINTEXT manifest (the dump itself is
    encrypted) — the asymmetry is why the artifact looked fine."""
    import base64 as _b64

    monkeypatch.setenv(
        "TORTOISE_BACKUP_KEY", _b64.b64encode(os.urandom(32)).decode())
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp, "src.db")
        _seed_points_and_marker_edges(proj.g)
        registry = proj.db.select_graph("registry_3895")
        registry.query("MATCH (n) DETACH DELETE n")
        registry.query("CREATE (t:Team {id:'org_3895'})")

        store = MemoryStorage()
        manifest = create_backup(
            proj, registry, store, org_id="org_3895", graph_name="tortoise")

        assert manifest["node_count"] == 2
        assert manifest["edge_count"] == 1
        assert manifest["excluded_node_count"] == 3
        assert manifest["skipped_edge_count"] == 3
        assert manifest["unresolved_edge_count"] == 0
        assert manifest["dump_revision"] == 2

        # And the manifest is what the restore links: the artifact restores
        # edge_count/edge_count.
        import json as _json

        from tortoise.hosted_backup import decrypt_backup
        key = _b64.b64decode(os.environ["TORTOISE_BACKUP_KEY"])
        blob = store.download(f"backups/{manifest['backup_id']}/dump.enc")
        payload = _json.loads(decrypt_backup(blob, key=key))
        assert payload["node_count"] == manifest["node_count"]
        assert payload["edge_count"] == manifest["edge_count"]
        with tempfile.TemporaryDirectory() as tmp2:
            dst = _make_proj(tmp2, "dst.db")
            counts = restore_graph(dst.g, payload)
            assert counts == {"nodes": manifest["node_count"],
                              "edges": manifest["edge_count"]}
            dst.close()
        proj.close()


# ── the write-side race the filter alone would turn into data loss ─────────


class _RaceInjector:
    """Graph-handle proxy that mutates the graph BETWEEN ``dump_graph``'s two
    reads (node read, then edge read) — a real node+edge created mid-window.

    A filter-only fix would DROP that edge as 'unresolved' — real user-data
    loss. The writer must reconcile the endpoint instead (re-read it and
    export it)."""

    def __init__(self, g, inject_sql: str):
        self._g = g
        self._inject_sql = inject_sql
        self._injected = False

    def query(self, q, **kw):
        if not self._injected and "MATCH (a)-[r]->(b)" in q:
            self._injected = True
            self._g.query(self._inject_sql)
        return self._g.query(q, **kw)

    def __getattr__(self, name):
        return getattr(self._g, name)


def test_endpoint_created_mid_read_is_reconciled_not_dropped():
    """A node created between the node read and the edge read is exported (and
    its edge links) — the filter must never turn the read-window race into
    silent data loss."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp, "src.db")
        proj.g.query("CREATE (p:Point {id:'p1', content:'one', pointKind:'claim'})")
        inject = (
            "CREATE (late:Point {id:'late', content:'late', pointKind:'claim'}) "
            "WITH late MATCH (p:Point {id:'p1'}) "
            "CREATE (late)-[:IMPL {weight:0.1}]->(p)"
        )
        dump = dump_graph(_RaceInjector(proj.g, inject), graph_name="tortoise")

        ids = {n["dump_id"] for n in dump["nodes"]}
        late = [n for n in dump["nodes"] if n["props"].get("id") == "late"]
        assert late, "the mid-read node was dropped instead of reconciled"
        assert all(e["src"] in ids and e["dst"] in ids for e in dump["edges"])
        assert dump["unresolved_edge_count"] == 0, (
            "a real edge was silently dropped as unresolved"
        )
        # And it is a faithful, restorable artifact.
        with tempfile.TemporaryDirectory() as tmp2:
            dst = _make_proj(tmp2, "dst.db")
            counts = restore_graph(dst.g, dump)
            assert counts == {"nodes": dump["node_count"],
                              "edges": dump["edge_count"]}
            assert ("late", "IMPL", "p1") in _edge_uids(dst.g)
            dst.close()
        proj.close()


def test_skipped_nodes_are_still_skipped_by_the_same_predicate():
    """The node predicate is unchanged — the fix aligns the edge side to it,
    it does not loosen the exclusion (#1625 semantics preserved)."""
    assert _is_export_skip_node(["GraphEventMeta"], {}) is True
    assert _is_export_skip_node(["TeamMeta"], {}) is True
    assert _is_export_skip_node(["EpMeta"], {}) is True
    assert _is_export_skip_node(["Meta"], {"key": "point_fts_v2"}) is True
    assert _is_export_skip_node(["Meta"], {"key": "event_fts_v2"}) is True
    # DATA, not bookkeeping: calibration is Gate B state and must survive.
    assert _is_export_skip_node(["Meta"], {"key": "calibration_milestone"}) is False
    assert _is_export_skip_node(["Point"], {"id": "p1"}) is False


# ── the production DR entry point: the swap helper ─────────────────────────


def test_swap_helper_salvages_a_legacy_payload_and_reports_the_drops():
    """AC2 at the REAL production entry point (``restore_backup`` →
    ``_restore_into_temp_verify_swap``): a legacy artifact (edge list over the
    unfiltered set) can be restored with the linkable edges, and the dropped
    edges are counted + their endpoints returned in the result — the operator
    sees them, they are not swallowed."""
    from tortoise.hosted_backup import _restore_into_temp_verify_swap

    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp, "src.db")
        # The legacy artifact shape: 2 nodes, 2 edges, one referencing a node
        # the (pre-fix) node list omitted — edge_count is the UNFILTERED count.
        payload = {
            "format": DUMP_FORMAT,
            "graph_name": "legacy_live",
            "nodes": [
                {"dump_id": 1, "labels": ["Point"], "props": {"id": "p1"}},
                {"dump_id": 3, "labels": ["Point"], "props": {"id": "p3"}},
            ],
            "edges": [
                {"src": 1, "dst": 3, "type": "IMPL", "props": {"weight": 1.0}},
                {"src": 1, "dst": 2, "type": "STALE_BOOKKEEPING", "props": {}},
            ],
            "node_count": 2,
            "edge_count": 2,
        }
        live = f"legacy_live_{uuid4().hex[:8]}"
        try:
            result = _restore_into_temp_verify_swap(
                proj.db, payload, live_name=live, allow_dangling_edges=True)

            assert result["restored"]["edges"] == 1
            assert result["restored"]["dropped_edges"] == 1
            assert result["restored"]["dropped_edge_endpoints"] == [2]
            # The live graph carries the real edge — the salvage is not a wipe.
            assert ("p1", "IMPL", "p3") in _edge_uids(proj.db.select_graph(live))
        finally:
            import contextlib

            with contextlib.suppress(Exception):
                proj.db.select_graph(live).delete()
            proj.close()


def test_wedged_swap_salvages_a_legacy_payload_fork_free(monkeypatch):
    """P1 (review cycle 2): the fork-free promotion is the LAST-RESORT path a
    wedged swap falls back to, so it must salvage a legacy payload exactly as
    the temp verify did. It used to re-run the STRICT restore — refusing
    ``Edge restore incomplete`` in exactly the wedge mode the salvage exists
    for, after the live graph had already been deleted.

    This forces the wedge branch (every ``GRAPH.COPY`` raises
    ``ForkSlotWedgedError``) and asserts the promotion completes, reports the
    drops, and installs the linkable edge in the live graph.

    Mutation (RED): drop ``allow_dangling_edges`` from the promotion's
    ``restore_graph`` call, or drop the ``dropped_edges`` term from its count
    check — either way the promotion raises and this test fails.
    """
    import contextlib

    import tortoise.hosted_backup as hb
    from tortoise.fork_slot import ForkSlotRecovery, ForkSlotWedgedError

    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp, "src.db")
        # The legacy (rev-1) artifact shape: 2 nodes, 2 edges, one referencing
        # a node the (pre-fix) node list omitted — edge_count is UNFILTERED.
        payload = {
            "format": DUMP_FORMAT,
            "graph_name": "legacy_wedged",
            "nodes": [
                {"dump_id": 1, "labels": ["Point"], "props": {"id": "p1"}},
                {"dump_id": 3, "labels": ["Point"], "props": {"id": "p3"}},
            ],
            "edges": [
                {"src": 1, "dst": 3, "type": "IMPL", "props": {"weight": 1.0}},
                {"src": 1, "dst": 2, "type": "STALE_BOOKKEEPING", "props": {}},
            ],
            "node_count": 2,
            "edge_count": 2,
        }
        live = f"legacy_wedged_{uuid4().hex[:8]}"

        def _always_wedged(*args, **kwargs):
            raise ForkSlotWedgedError(
                site=kwargs.get("site", "restore swap"), dst_name=live,
                recovery=ForkSlotRecovery(
                    wedged=True, recovered=False,
                    detail="held by a foreign child"),
            )

        monkeypatch.setattr(hb, "_graph_copy_or_diagnose", _always_wedged)
        try:
            result = hb._restore_into_temp_verify_swap(
                proj.db, payload, live_name=live, allow_dangling_edges=True)

            # The salvage completed instead of dying in the wedge's own mode.
            assert result["restored"]["edges"] == 1
            assert result["restored"]["dropped_edges"] == 1
            assert result["restored"]["dropped_edge_endpoints"] == [2]
            assert result["fork_slot"]["wedged"] is True
            # The LIVE graph — not just the temp graph — carries the real
            # edge: the salvage is not a wipe and not a no-op.
            assert ("p1", "IMPL", "p3") in _edge_uids(
                proj.db.select_graph(live))
        finally:
            with contextlib.suppress(Exception):
                proj.db.select_graph(live).delete()
            proj.close()


def test_swap_helper_still_refuses_a_legacy_payload_by_default():
    """Same payload, no opt-in: the swap must refuse (fail closed) so a drill
    keeps reporting the artifact as broken instead of quietly salvaging."""
    from tortoise.hosted_backup import _restore_into_temp_verify_swap

    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp, "src.db")
        payload = {
            "format": DUMP_FORMAT,
            "graph_name": "legacy_live2",
            "nodes": [{"dump_id": 1, "labels": ["Point"], "props": {"id": "p1"}}],
            "edges": [{"src": 1, "dst": 2, "type": "STALE_BOOKKEEPING", "props": {}}],
            "node_count": 1,
            "edge_count": 1,
        }
        live = f"legacy_live2_{uuid4().hex[:8]}"
        with pytest.raises(ValueError, match="Edge restore incomplete"):
            _restore_into_temp_verify_swap(
                proj.db, payload, live_name=live)
        # Nothing was swapped in — the live graph was never created. (Asserted
        # via the graph handle, not a global name scan: a no-op read on a
        # non-existent graph must not fabricate state.)
        assert proj.db.select_graph(live).query(
            "MATCH (n) RETURN count(n)").result_set == [[0]]
        proj.close()


# ── review-cycle fixes: the branches the filter could turn into data loss ──


class _TornNodeRead:
    """Hides ONE live node from every property-returning node read (the node
    read AND the reconcile read) while an ``id(n)``-existence probe still sees
    it — the truncated-read shape that a filter-only fix would turn into silent
    data loss: the edge is exported, its endpoint is not, and the dump would
    look complete."""

    def __init__(self, g, hidden_id: int):
        self._g = g
        self._hidden = hidden_id

    def query(self, q, **kw):
        res = self._g.query(q, **kw)
        if "labels(n)" in q and "properties(n)" in q:
            return _Rows([r for r in res.result_set if int(r[0]) != self._hidden])
        return res

    def __getattr__(self, name):
        return getattr(self._g, name)


class _Rows:
    """Minimal stand-in for the client's result object (``result_set`` only)."""

    def __init__(self, rows):
        self.result_set = rows


class _PhantomEdge:
    """Adds one edge row whose endpoint does NOT exist — the 'node deleted
    between the two reads' shape (unresolvable AND confirmed absent)."""

    def __init__(self, g, src_id: int, phantom_id: int):
        self._g = g
        self._src = src_id
        self._phantom = phantom_id

    def query(self, q, **kw):
        res = self._g.query(q, **kw)
        if q.lstrip().startswith("MATCH (a)-[r]->(b)"):
            return _Rows([*res.result_set,
                          [self._src, self._phantom, "STALE_BOOKKEEPING", {}]])
        return res

    def __getattr__(self, name):
        return getattr(self._g, name)


def test_torn_node_read_raises_instead_of_writing_a_lossy_dump():
    """P0 (review cycle 1): a node that is LIVE but absent from the read must
    ABORT the dump. Dropping its edge would write an artifact that restores
    'green' with real data missing — the exact 'looks verified and is not'
    class #3895 is about.

    Mutation (RED): classify live-but-unreturned endpoints as skippable (drop
    the `_first_live_node_id` check) -> no raise, and the edge is gone from a
    dump whose restore succeeds."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp, "src.db")
        proj.g.query("CREATE (p:Point {id:'p1', content:'one', pointKind:'claim'})")
        proj.g.query("CREATE (p:Point {id:'p2', content:'two', pointKind:'claim'})")
        proj.g.query(
            "MATCH (a:Point {id:'p1'}), (b:Point {id:'p2'}) "
            "CREATE (a)-[:IMPL {weight:0.9}]->(b)"
        )
        hidden = int(proj.g.query(
            "MATCH (n:Point {id:'p2'}) RETURN id(n)").result_set[0][0])

        with pytest.raises(ValueError, match="LIVE"):
            dump_graph(_TornNodeRead(proj.g, hidden), graph_name="tortoise")
        proj.close()


def test_stale_edge_from_a_deleted_endpoint_is_dropped_and_counted():
    """The complementary branch: an endpoint that is unresolvable AND probed
    ABSENT (deleted between the two reads) makes the edge stale, so dropping it
    is faithful — the dump still writes, with the drop COUNTED (never silent)
    and the artifact still restorable."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp, "src.db")
        proj.g.query("CREATE (p:Point {id:'p1', content:'one', pointKind:'claim'})")
        src_id = int(proj.g.query(
            "MATCH (n:Point {id:'p1'}) RETURN id(n)").result_set[0][0])

        dump = dump_graph(
            _PhantomEdge(proj.g, src_id, phantom_id=999_999),
            graph_name="tortoise",
        )
        assert dump["unresolved_edge_count"] == 1
        assert dump["skipped_edge_count"] == 0
        assert dump["edge_count"] == 0
        with tempfile.TemporaryDirectory() as tmp2:
            dst = _make_proj(tmp2, "dst.db")
            assert restore_graph(dst.g, dump) == {"nodes": 1, "edges": 0}
            dst.close()
        proj.close()


def test_non_integer_ids_raise_valueerror_not_typeerror():
    """The dump contract is INTEGER ids (``dump_graph`` int()s them; both JSON
    pipelines preserve ints), so a string/float/bool/null id is CORRUPTION, not
    a coercion case.

    P1 (review cycle 1): a malformed id must be a pre-restore ValueError
    (the import endpoint maps it to a 422 + quarantine), never an uncaught
    TypeError that becomes a 500 with no quarantine record."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp, "dst.db")
        good_node = {"dump_id": 1, "labels": ["Point"], "props": {"id": "p1"}}
        for bad in ([], {}, "x", 1.5, True, None):
            with pytest.raises(ValueError):
                restore_graph(proj.g, {
                    "format": DUMP_FORMAT,
                    "nodes": [good_node],
                    "edges": [{"src": bad, "dst": 1, "type": "IMPL", "props": {}}],
                })
            with pytest.raises(ValueError):
                restore_graph(proj.g, {
                    "format": DUMP_FORMAT,
                    "nodes": [good_node],
                    "edges": [{"src": 1, "dst": bad, "type": "IMPL", "props": {}}],
                })
            with pytest.raises(ValueError):
                restore_graph(proj.g, {
                    "format": DUMP_FORMAT,
                    "nodes": [{"dump_id": bad, "labels": ["Point"], "props": {}}],
                    "edges": [],
                })
        # Non-list containers and non-dict prop bags are corruption too — and
        # must not escape as a TypeError (a 500 with no quarantine record).
        for malformed in (
            {"format": DUMP_FORMAT, "nodes": None, "edges": []},
            {"format": DUMP_FORMAT, "nodes": 5, "edges": []},
            {"format": DUMP_FORMAT, "nodes": [], "edges": None},
            {"format": DUMP_FORMAT, "nodes": [], "edges": {}},
            {"format": DUMP_FORMAT,
             "nodes": [{"dump_id": 1, "labels": ["Point"], "props": 5}],
             "edges": []},
            {"format": DUMP_FORMAT,
             "nodes": [good_node],
             "edges": [{"src": 1, "dst": 1, "type": "IMPL", "props": 5}]},
            {"format": DUMP_FORMAT,
             "nodes": [{"dump_id": 1, "labels": 5, "props": {}}],
             "edges": []},
        ):
            with pytest.raises(ValueError):
                restore_graph(proj.g, malformed)

        # Nothing was created by any rejected attempt (the projection's own
        # :Meta{point_fts_v2} marker may exist — it is bookkeeping, not a
        # restored Point).
        assert proj.g.query(
            "MATCH (n:Point) RETURN count(n)").result_set[0][0] == 0
        proj.close()


def test_restore_graph_refuses_a_non_empty_target():
    """P1 (review cycle 1): restore_graph REBUILDS a graph; it does not merge
    into one. A dirty target would make the returned node count describe a
    different population than the edge count (and the graph-wide `__dump_id`
    cleanup could clobber pre-existing nodes)."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp, "dst.db")
        proj.g.query("CREATE (p:Point {id:'pre-existing', pointKind:'claim'})")
        dump = {
            "format": DUMP_FORMAT,
            "nodes": [{"dump_id": 1, "labels": ["Point"], "props": {"id": "p1"}}],
            "edges": [],
        }
        with pytest.raises(ValueError, match="not empty"):
            restore_graph(proj.g, dump)
        # The pre-existing graph is untouched by the refusal.
        assert proj.g.query(
            "MATCH (n:Point) RETURN n.id").result_set == [["pre-existing"]]
        proj.close()


def test_a_rev2_artifact_refuses_salvage():
    """P2 (review cycle 1): a rev-2 artifact's writer restricts both halves to
    one node set, so it can never emit a dangling edge — salvaging one would
    mask CORRUPTION as a legacy artifact. Refuse."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp, "dst.db")
        tampered = {
            "format": DUMP_FORMAT,
            "dump_revision": 2,
            "nodes": [{"dump_id": 1, "labels": ["Point"], "props": {"id": "p1"}}],
            "edges": [{"src": 1, "dst": 2, "type": "IMPL", "props": {}}],
        }
        with pytest.raises(ValueError, match="dump_revision 2"):
            restore_graph(proj.g, tampered, allow_dangling_edges=True)
        # Without salvage it is still the ordinary fail-closed refusal.
        with pytest.raises(ValueError, match="Edge restore incomplete"):
            restore_graph(proj.g, tampered)
        proj.close()


def test_dump_declares_the_revision_that_makes_salvage_refusable():
    """The revision marker is what lets a reader refuse salvage on a
    post-fix artifact; a dump without it is pre-fix (rev 1) by definition."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp, "src.db")
        proj.g.query("CREATE (p:Point {id:'p1', pointKind:'claim'})")
        dump = dump_graph(proj.g, graph_name="t")
        assert dump["dump_revision"] == 2
        proj.close()


def test_tolerated_skip_marker_edge_does_not_false_fire_the_invariant():
    """P2 (review cycle 2): the empty-target check tolerates export-skip
    bookkeeping, but a tolerated marker may CARRY an edge (a legacy graph).
    That edge is not part of this restore, so it must not be counted as one —
    otherwise a valid dump is rejected ('dump/restore disagree')."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp, "dst.db")
        proj.g.query("MERGE (m:GraphEventMeta) SET m.last_seq = 1")
        proj.g.query("MATCH (m:GraphEventMeta) CREATE (m)-[:SELF]->(m)")

        dump = {
            "format": DUMP_FORMAT,
            "nodes": [{"dump_id": 1, "labels": ["Point"], "props": {"id": "p1"}}],
            "edges": [{"src": 1, "dst": 1, "type": "IMPL", "props": {}}],
        }
        assert restore_graph(proj.g, dump) == {"nodes": 1, "edges": 1}
        proj.close()
