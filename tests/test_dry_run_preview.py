"""Dry-run previews on destructive MCP tools (#4057).

The contract has two halves, and the second is the one that can lie:

* ``dry_run=False`` (the default) is today's behaviour — the default path
  must never reach the preview code at all.
* ``dry_run=True`` reports the concrete blast radius and changes NOTHING.
  Every test snapshots the graph (node count, edge count, target status)
  before the preview and asserts it unchanged after: a dry run that still
  writes is worse than no dry run, because it lies.
* Where a preview mirrors a non-trivial writer rule (supersede's edge
  transfer), a DIFFERENTIAL test pins the preview's count against the
  writer's own ``edges_transferred``.

Runnable with:
  TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \
    .venv/bin/python -m pytest tests/test_dry_run_preview.py -v
"""
from __future__ import annotations

import datetime as _dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.mcp_auth import (
    _current_org_id,
    _current_org_limits,
    _transport_mode,
)
from tortoise.sdk import TortoiseSDK

DESTRUCTIVE_TOOLS = (
    "tortoise_delete",
    "tortoise_delete_point",
    "tortoise_delete_entity",
    "tortoise_supersede",
    "tortoise_invalidate",
    "tortoise_retract_point",
)

_PREVIEW_FUNCS = (
    "_preview_delete",
    "_preview_delete_point",
    "_preview_delete_entity",
    "_preview_supersede",
    "_preview_invalidate",
    "_preview_retract_point",
)


@pytest.fixture
def sdk(tmp_path):
    # `tmp_path` (not `mkdtemp`) — pytest reclaims it, so this fixture cannot leak a tree
    # per test (#4096: 49 fixtures leaked 5,725 dirs before that guard existed).
    s = TortoiseSDK(str(tmp_path / "test.db"))
    yield s
    s.close()


@pytest.fixture
def mcp(sdk, monkeypatch):
    """MCP module with the org SDK pinned to the temp embedded graph and the
    stdio transport set (the same bed test_mcp_server.py uses)."""
    import tortoise.mcp_server as m

    _transport_mode.set("stdio")
    _current_org_id.set(None)
    _current_org_limits.set(None)
    monkeypatch.setattr(m, "_get_org_sdk", lambda: sdk)
    yield m
    _transport_mode.set(None)
    _current_org_id.set(None)
    _current_org_limits.set(None)


def _graph_counts(sdk: TortoiseSDK) -> tuple[int, int]:
    proj = sdk._get_proj()
    nodes = proj.g.query("MATCH (n) RETURN count(n)").result_set[0][0]
    edges = proj.g.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
    return nodes, edges


def _point_props(sdk: TortoiseSDK, point_id: str) -> dict | None:
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN properties(n)",
        params={"id": point_id},
    ).result_set
    return rows[0][0] if rows else None


def _edge_keys_at(sdk: TortoiseSDK, point_id: str) -> set:
    """Identity of every edge incident to the point, excluding CORRECTS."""
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$n})-[r]-(m) WHERE type(r) <> 'CORRECTS' "
        "RETURN ID(r), type(r), "
        "coalesce(startNode(r).id, startNode(r).eventId, startNode(r).name, startNode(r).url), "
        "coalesce(endNode(r).id, endNode(r).eventId, endNode(r).name, endNode(r).url)",
        params={"n": point_id},
    ).result_set
    return {(r[0], r[1], r[2], r[3]) for r in rows}


def _seed_point(sdk: TortoiseSDK, content: str = "seed") -> str:
    return sdk.create_point("statement", content)["id"]


# ── A. the default path is unchanged ────────────────────────────────

class TestDefaultPathUnchanged:
    """`dry_run=False` must not touch the preview code — if it did, the
    "byte-identical" claim would be an assertion rather than a fact.

    The guard has to be OBSERVABLE IN THE HANDLER'S RETURN VALUE. A preview
    that RAISES cannot prove anything here: every preview call is wrapped in
    `_safe`, whose `except Exception` converts the raise into
    `{"error": ...}` — so a preview reached on the default path is
    indistinguishable from a benign failure unless the result is inspected.
    Each preview therefore returns a UNIQUE sentinel, and the assertions below
    read the result: a reached preview shows up as the handler's own return
    value, and the expected default SHAPES pin that the default path really
    did its work (so "no sentinel" cannot be satisfied by an error dict).
    """

    @staticmethod
    def _sentinel_for(name: str):
        def _sentinel(*a, **k):
            return {"preview_sentinel": name}
        return _sentinel

    def _install_sentinels(self, mcp, monkeypatch):
        for name in _PREVIEW_FUNCS:
            monkeypatch.setattr(mcp, name, self._sentinel_for(name))

    def _default_path_calls(self, mcp, *, explicit_false: bool):
        """Call all six destructive tools on the default path."""
        sdk = mcp._get_org_sdk()
        ids = {
            "a": _seed_point(sdk, "a"),
            "b": _seed_point(sdk, "b"),
            "c": _seed_point(sdk, "c"),
            "d": _seed_point(sdk, "d"),
            "e": _seed_point(sdk, "e"),
            "f": _seed_point(sdk, "f"),
            "g": _seed_point(sdk, "g"),
        }
        ids["subj"] = sdk.create_entity(
            "subject", "S", subjectKind="company")["node"]["id"]
        kw = {"dry_run": False} if explicit_false else {}
        results = {
            "tortoise_delete_point": mcp.tortoise_delete_point(ids["a"], **kw),
            "tortoise_delete": mcp.tortoise_delete(ids["b"], **kw),
            "tortoise_delete_entity": mcp.tortoise_delete_entity(ids["subj"], **kw),
            "tortoise_retract_point": mcp.tortoise_retract_point(ids["c"], **kw),
            "tortoise_invalidate": mcp.tortoise_invalidate(ids["d"], ids["e"], **kw),
            "tortoise_supersede": mcp.tortoise_supersede(ids["f"], ids["g"], **kw),
        }
        return ids, results

    def _assert_default_path(self, ids, results):
        for tool, result in results.items():
            if isinstance(result, dict):
                assert "preview_sentinel" not in result, (
                    f"{tool} reached its dry-run preview on the default path: "
                    f"{result}")
                assert "dry_run" not in result, (
                    f"{tool} returned a dry-run preview on the default path: "
                    f"{result}")
        # The default path's OWN return shapes — a result that merely avoids
        # the sentinel (e.g. an error dict) must not pass.
        assert results["tortoise_delete_point"] == {
            "deleted": True, "id": ids["a"]}, results["tortoise_delete_point"]
        assert results["tortoise_delete"] == {
            "deleted": True, "id": ids["b"]}, results["tortoise_delete"]
        assert results["tortoise_delete_entity"] is True, (
            results["tortoise_delete_entity"])
        assert results["tortoise_retract_point"]["status"] == "retracted", (
            results["tortoise_retract_point"])
        assert results["tortoise_invalidate"]["invalidated"] is True, (
            results["tortoise_invalidate"])
        assert results["tortoise_supersede"]["invalidated"] is True, (
            results["tortoise_supersede"])

    def test_previews_are_unreachable_when_dry_run_is_omitted(
            self, mcp, monkeypatch):
        self._install_sentinels(mcp, monkeypatch)
        ids, results = self._default_path_calls(mcp, explicit_false=False)
        self._assert_default_path(ids, results)

    def test_previews_are_unreachable_when_dry_run_is_false(
            self, mcp, monkeypatch):
        self._install_sentinels(mcp, monkeypatch)
        ids, results = self._default_path_calls(mcp, explicit_false=True)
        self._assert_default_path(ids, results)

    def test_dry_run_defaults_to_false_in_every_signature(self):
        import tortoise.mcp_server as m
        for tool in DESTRUCTIVE_TOOLS:
            sig = inspect.signature(getattr(m, tool))
            assert "dry_run" in sig.parameters, tool
            assert sig.parameters["dry_run"].default is False, tool

    def test_default_delete_result_carries_no_preview_keys(self, mcp):
        pid = _seed_point(mcp._get_org_sdk(), "bye")
        result = mcp.tortoise_delete_point(pid)
        assert result == {"deleted": True, "id": pid}
        for key in ("dry_run", "would", "note", "edges"):
            assert key not in result

    def test_default_delete_entity_returns_a_bool(self, mcp):
        oid = mcp._get_org_sdk().create_entity(
            "object", "O", objectKind="product")["node"]["id"]
        assert mcp.tortoise_delete_entity(oid) is True


# ── B. dry_run=True writes nothing ──────────────────────────────────

class TestDryRunWritesNothing:
    """One test per tool: snapshot the graph, preview, assert unchanged."""

    def _assert_unchanged(self, sdk, before, result):
        assert result.get("dry_run") is True, result
        assert _graph_counts(sdk) == before, (
            "the dry run mutated the graph — a preview that writes lies")

    def test_delete_point_preview(self, mcp, sdk):
        a = _seed_point(sdk, "a")
        subj = sdk.create_entity("subject", "S", subjectKind="company")["node"]["id"]
        sdk.create_edge("aboutSubject", a, subj)
        before = _graph_counts(sdk)
        before_props = _point_props(sdk, a)

        result = mcp.tortoise_delete_point(a, dry_run=True)

        self._assert_unchanged(sdk, before, result)
        assert _point_props(sdk, a) == before_props
        assert result["nodes_removed"] == 1
        assert result["nodes"] == [a]
        assert any(e["type"] == "aboutSubject" for e in result["edges"])
        assert result["edges_removed"] == len(result["edges"])

    def test_delete_preview_on_a_point(self, mcp, sdk):
        a = _seed_point(sdk, "a")
        before = _graph_counts(sdk)
        result = mcp.tortoise_delete(a, dry_run=True)
        self._assert_unchanged(sdk, before, result)
        assert result["target"]["label"] == "Point"
        assert result["found"] is True

    def test_delete_preview_on_an_entity(self, mcp, sdk):
        oid = sdk.create_entity("object", "O", objectKind="product")["node"]["id"]
        before = _graph_counts(sdk)
        result = mcp.tortoise_delete(oid, dry_run=True)
        self._assert_unchanged(sdk, before, result)
        assert result["found"] is True

    def test_delete_preview_on_a_missing_id(self, mcp, sdk):
        before = _graph_counts(sdk)
        result = mcp.tortoise_delete("no-such-id", dry_run=True)
        self._assert_unchanged(sdk, before, result)
        assert result["found"] is False
        assert result["nodes_removed"] == 0

    def test_delete_entity_preview(self, mcp, sdk):
        oid = sdk.create_entity("object", "O", objectKind="product")["node"]["id"]
        before = _graph_counts(sdk)
        result = mcp.tortoise_delete_entity(oid, dry_run=True)
        self._assert_unchanged(sdk, before, result)
        assert result["nodes_removed"] == 1
        assert result["nodes"] == [oid]

    def test_retract_preview(self, mcp, sdk):
        p = _seed_point(sdk, "retract me")
        before = _graph_counts(sdk)
        result = mcp.tortoise_retract_point(p, dry_run=True)
        self._assert_unchanged(sdk, before, result)
        assert _point_props(sdk, p)["status"] != "retracted"
        assert result["status"]["to"] == "retracted"

    def test_invalidate_preview(self, mcp, sdk):
        old, new = _seed_point(sdk, "old"), _seed_point(sdk, "new")
        before = _graph_counts(sdk)
        result = mcp.tortoise_invalidate(old, new, dry_run=True)
        self._assert_unchanged(sdk, before, result)
        assert _point_props(sdk, old).get("outdated") is not True
        assert result["edges_added"] == 1
        assert result["edges"][0]["type"] == "CORRECTS"

    def test_supersede_preview_transfer_edges(self, mcp, sdk):
        old, new = _seed_point(sdk, "old"), _seed_point(sdk, "new")
        x = _seed_point(sdk, "x")
        sdk.create_direct_edge("IMPL", old, x)
        before = _graph_counts(sdk)
        result = mcp.tortoise_supersede(old, new, dry_run=True)
        self._assert_unchanged(sdk, before, result)
        assert _point_props(sdk, old).get("outdated") is not True
        assert result["edges_transferred"] >= 1
        assert result["transfer_edges"] is True

    def test_supersede_preview_no_transfer(self, mcp, sdk):
        old, new = _seed_point(sdk, "old"), _seed_point(sdk, "new")
        before = _graph_counts(sdk)
        result = mcp.tortoise_supersede(old, new, transfer_edges=False,
                                        dry_run=True)
        self._assert_unchanged(sdk, before, result)
        assert result["transfer_edges"] is False
        assert result["edges_added"] == 1


# ── C. blast radius / differential pin ──────────────────────────────

class TestBlastRadius:
    def test_delete_point_preview_reports_edge_direction(self, mcp, sdk):
        """An incoming edge must not be reported with the deleted node as its
        source — the blast radius is only useful if the orientation is true."""
        a = _seed_point(sdk, "a")
        subj = sdk.create_entity(
            "subject", "S", subjectKind="company")["node"]["id"]
        sdk.create_edge("aboutSubject", a, subj)  # a -> subj
        preview = mcp.tortoise_delete_point(a, dry_run=True)
        assert preview["edges"] == [
            {"type": "aboutSubject", "from": a, "to": subj}], preview

    def test_supersede_preview_reports_the_new_edge_endpoints(self, mcp, sdk):
        old, new = _seed_point(sdk, "old"), _seed_point(sdk, "new")
        x = _seed_point(sdk, "x")
        sdk.create_direct_edge("IMPL", old, x)  # old -> x, becomes new -> x
        preview = mcp.tortoise_supersede(old, new, dry_run=True)
        assert {"type": "IMPL", "from": new, "to": x} in preview["edges"]

    def test_supersede_preview_collapses_parallel_edges(self, sdk):
        """Parallel same-type edges collapse to ONE at the successor (the
        writer MERGEs), so the preview must report the raw old-side count AND
        the deduped successor count — not the source-row count as if each
        became a distinct edge. (Parallel edges are raw-Cypher only; no SDK
        write path mints them.)"""
        old, new = _seed_point(sdk, "old"), _seed_point(sdk, "new")
        subj = sdk.create_entity(
            "subject", "S", subjectKind="company")["node"]["id"]
        sdk._get_proj().g.query(
            "MATCH (o:Point {id:$o}), (t:Subject {id:$t}) "
            "CREATE (o)-[:aboutSubject]->(t), (o)-[:aboutSubject]->(t)",
            params={"o": old, "t": subj},
        )
        from tortoise.mcp_server import _preview_supersede
        preview = _preview_supersede(sdk, old, new)
        assert preview["edges_transferred_from_old"] == 2
        assert preview["edges_created_at_new"] == 1
        real = sdk.supersede(old, new)
        assert preview["edges_created_at_new"] == real["edges_transferred"]

    def test_supersede_preview_keeps_parallel_operator_edges(self, sdk):
        """The 2a operator leg uses CREATE, NOT MERGE: `create_operator(op,
        old, [old])` mints two IMPL edges (idx 0 and idx 1), and the writer
        creates TWO edges at the successor. The dedup must not collapse this
        leg — that would under-report the blast radius."""
        old, new = _seed_point(sdk, "old"), _seed_point(sdk, "new")
        sdk.create_operator("IMPL", old, [old])
        from tortoise.mcp_server import _preview_supersede
        preview = _preview_supersede(sdk, old, new)
        assert preview["edges_created_at_new"] == 2, preview
        real = sdk.supersede(old, new)
        assert real["edges_transferred"] == 2

    def test_delete_entity_preview_dedups_a_multi_label_node(self, mcp, sdk):
        """A `:Point:Object` node is deleted ONCE (the writer's first
        DETACH DELETE removes it, the next label matches 0) — the preview
        must not count it per label."""
        sdk._get_proj().g.query(
            "CREATE (n:Point:Object {id:'multi-1', content:'x', "
            "pointKind:'statement'})")
        preview = mcp.tortoise_delete_entity("multi-1", dry_run=True)
        assert preview["nodes_removed"] == 1, preview
        assert preview["nodes"] == ["multi-1"]

    def test_supersede_preview_rejects_an_undeclared_rel_type(self, mcp, sdk):
        """The writer validates relationship types before mutating (#329);
        the preview must reject the same input rather than preview a happy
        path the write would refuse."""
        old, new = _seed_point(sdk, "old"), _seed_point(sdk, "new")
        sdk._get_proj().g.query(
            "MATCH (o:Point {id:$o}) CREATE (p:Point {is_operator:true, "
            "id:'op-evil'})-[:EVIL_REL]->(o)", params={"o": old})
        before = _graph_counts(sdk)
        result = mcp.tortoise_supersede(old, new, dry_run=True)
        assert result.get("error"), result
        assert _graph_counts(sdk) == before

    def test_delete_point_preview_counts_all_matching_nodes_and_edges(
            self, mcp, sdk):
        """The writer's DETACH DELETE removes EVERY matching Point, and each
        node's edges must be enumerated once (internal-id keyed) — a logical-id
        edge match would return both nodes' edges for each node."""
        proj = sdk._get_proj()
        proj.g.query(
            "CREATE (a:Point {id:'dup-9', content:'a', pointKind:'statement'})")
        proj.g.query(
            "CREATE (b:Point {id:'dup-9', content:'b', pointKind:'statement'})")
        subj = sdk.create_entity(
            "subject", "S", subjectKind="company")["node"]["id"]
        for (internal,) in proj.g.query(
                "MATCH (n:Point {id:'dup-9'}) RETURN ID(n)").result_set:
            proj.g.query(
                "MATCH (a), (s:Subject {id:$s}) WHERE ID(a) = $nid "
                "CREATE (a)-[:aboutSubject]->(s)",
                params={"nid": internal, "s": subj})
        preview = mcp.tortoise_delete_point("dup-9", dry_run=True)
        assert preview["nodes_removed"] == 2, preview
        assert preview["edges_removed"] == 2, preview
        assert len(preview["edges"]) == 2

    def test_delete_entity_preview_counts_duplicate_id_edges_once(
            self, mcp, sdk):
        proj = sdk._get_proj()
        proj.g.query(
            "CREATE (a:Point {id:'dup-e', content:'a', pointKind:'statement'})")
        proj.g.query(
            "CREATE (b:Point {id:'dup-e', content:'b', pointKind:'statement'})")
        subj = sdk.create_entity(
            "subject", "S", subjectKind="company")["node"]["id"]
        for (internal,) in proj.g.query(
                "MATCH (n:Point {id:'dup-e'}) RETURN ID(n)").result_set:
            proj.g.query(
                "MATCH (a), (s:Subject {id:$s}) WHERE ID(a) = $nid "
                "CREATE (a)-[:aboutSubject]->(s)",
                params={"nid": internal, "s": subj})
        preview = mcp.tortoise_delete_entity("dup-e", dry_run=True)
        assert preview["nodes_removed"] == 2, preview
        assert preview["edges_removed"] == 2, preview

    def test_delete_point_preview_dedups_an_edge_between_deleted_nodes(
            self, mcp, sdk):
        """An edge whose BOTH endpoints are in the delete set is removed once,
        not once per endpoint."""
        proj = sdk._get_proj()
        proj.g.query(
            "CREATE (a:Point {id:'dup-7', content:'a', pointKind:'statement'})")
        proj.g.query(
            "CREATE (b:Point {id:'dup-7', content:'b', pointKind:'statement'})")
        proj.g.query(
            "MATCH (a:Point {id:'dup-7'}), (b:Point {id:'dup-7'}) "
            "WHERE ID(a) < ID(b) CREATE (a)-[:related]->(b)")
        preview = mcp.tortoise_delete_point("dup-7", dry_run=True)
        assert preview["nodes_removed"] == 2, preview
        assert preview["edges_removed"] == 1, preview

    def test_delete_point_preview_reports_the_tag_gc_cascade(self, mcp, sdk):
        """`delete_point` also hard-deletes orphaned :Tag nodes when the point
        carried a tag — part of the blast radius."""
        p = sdk.create_point("statement", "tagged one", tags=["solo-tag"])["id"]
        preview = mcp.tortoise_delete_point(p, dry_run=True)
        assert preview["tags_removed"] >= 1, preview
        assert "solo-tag" in preview["tags"], preview

    def test_invalidate_preview_sees_a_pre_existing_corrects_edge(self, mcp, sdk):
        """The writer MERGEs CORRECTS, so a pre-existing edge adds nothing."""
        old, new = _seed_point(sdk, "old"), _seed_point(sdk, "new")
        sdk._get_proj().g.query(
            "MATCH (a:Point {id:$n}), (b:Point {id:$o}) CREATE (a)-[:CORRECTS]->(b)",
            params={"n": new, "o": old})
        preview = mcp.tortoise_invalidate(old, new, dry_run=True)
        assert preview["edges_added"] == 0, preview
        assert preview["edges"] == []

    def test_invalidate_preview_counts_duplicate_id_pairs(self, mcp, sdk):
        """The writer binds every (new, old) pair, so a duplicated old id is
        stamped on BOTH nodes and MERGEs an edge per pair."""
        proj = sdk._get_proj()
        new = _seed_point(sdk, "new")
        proj.g.query(
            "CREATE (a:Point {id:'olddup', content:'a', pointKind:'statement'})")
        proj.g.query(
            "CREATE (b:Point {id:'olddup', content:'b', pointKind:'statement'})")
        preview = mcp.tortoise_invalidate("olddup", new, dry_run=True)
        assert preview["nodes_affected"] == 2, preview
        assert preview["edges_added"] == 2, preview

    def test_supersede_preview_counts_a_duplicate_successor_id(self, sdk):
        """The writer's `MATCH (new:Point {id})` binds every matching node, so
        each transfer fans out to one edge per successor node."""
        proj = sdk._get_proj()
        old = _seed_point(sdk, "old")
        proj.g.query(
            "CREATE (n:Point {id:'newdup', content:'x', pointKind:'statement'})")
        proj.g.query(
            "CREATE (n:Point {id:'newdup', content:'y', pointKind:'statement'})")
        subj = sdk.create_entity(
            "subject", "S", subjectKind="company")["node"]["id"]
        sdk.create_edge("aboutSubject", old, subj)
        from tortoise.mcp_server import _preview_supersede
        preview = _preview_supersede(sdk, old, "newdup")
        assert preview["successor_nodes"] == 2, preview
        assert preview["edges_created_at_new"] == 2, preview

    def test_supersede_preview_excludes_a_pre_existing_successor_edge(self, sdk):
        """`new` already carrying the destination edge means the writer's MERGE
        is a no-op — the preview must not claim a net-new edge."""
        old, new = _seed_point(sdk, "old"), _seed_point(sdk, "new")
        x = _seed_point(sdk, "x")
        sdk.create_direct_edge("IMPL", new, x)   # already at the successor
        sdk.create_direct_edge("IMPL", old, x)   # transfers onto the same edge
        from tortoise.mcp_server import _preview_supersede
        preview = _preview_supersede(sdk, old, new)
        assert preview["edges_created_at_new"] == 0, preview
        assert preview["edges_already_present_at_new"] == 1, preview
        assert preview["edges_transferred_from_old"] == 1

    def test_delete_point_preview_lists_the_edges_the_delete_removes(
            self, mcp, sdk):
        a = _seed_point(sdk, "a")
        subj = sdk.create_entity("subject", "S", subjectKind="company")["node"]["id"]
        obj = sdk.create_entity("object", "O", objectKind="product")["node"]["id"]
        sdk.create_edge("aboutSubject", a, subj)
        sdk.create_edge("aboutObject", a, obj)
        preview = mcp.tortoise_delete_point(a, dry_run=True)
        preview_edges = {(e["type"], e["from"], e["to"]) for e in preview["edges"]}
        assert len(preview_edges) == 2, preview

        mcp.tortoise_delete_point(a)
        after = sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) RETURN count(n)", params={"id": a}
        ).result_set[0][0]
        assert after == 0
        # the preview's edge set was non-empty and the delete really removed
        # every edge it named
        remaining = sdk._get_proj().g.query(
            "MATCH (s:Point {id:$a})-[r]->() RETURN count(r)", params={"a": a}
        ).result_set[0][0]
        assert remaining == 0

    def test_supersede_preview_count_matches_the_writer(self, sdk):
        """Differential pin against GROUND TRUTH: the preview's
        `edges_added_at_new` must equal the edges that actually exist at the
        successor after the real supersede (minus the CORRECTS edge it adds),
        and its `edges_transferred_from_old` must equal the SDK's own
        `edges_transferred` on a parallel-edge-free graph. This is what stops
        the mirrored transfer rule from drifting into a preview that lies."""
        old, new = _seed_point(sdk, "old"), _seed_point(sdk, "new")
        x, y = _seed_point(sdk, "x"), _seed_point(sdk, "y")
        subj = sdk.create_entity("subject", "S", subjectKind="company")["node"]["id"]
        # an operator edge op -> old
        sdk.create_operator("IMPL", old, [x])
        # a direct IMPL/NAND pair, one in each direction
        sdk.create_direct_edge("IMPL", old, x)
        sdk.create_direct_edge("NAND", y, old)
        # a structural edge
        sdk.create_edge("aboutSubject", old, subj)
        # an alreadyDecided operator — must stay attached to the dead prior
        sdk.create_operator("IMPL", old, [y], label="alreadyDecided")
        # a self edge to the successor — delete-only, never repointed
        sdk.create_edge("aboutObject", old, new)

        from tortoise.mcp_server import _preview_supersede

        before_keys = _edge_keys_at(sdk, new)
        preview = _preview_supersede(sdk, old, new)
        real = sdk.supersede(old, new)

        net_new = _edge_keys_at(sdk, new) - before_keys
        assert preview["edges_created_at_new"] == len(net_new), (
            f"preview said {preview['edges_created_at_new']} net-new edges, "
            f"the graph gained {len(net_new)}")
        # The old-side count matches the SDK counter on a graph with no
        # parallel edges AND no direct self-loop at `old` (see the self-loop
        # leg below, where the writer double-books and the preview is right).
        assert preview["edges_transferred_from_old"] == real["edges_transferred"]
        assert preview["edges_created_at_new"] >= 4
        kept = {(e["from"], e["to"]) for e in preview["edges_kept_attached"]}
        assert any(t == old for _, t in kept), kept
        assert preview["edges_dropped"], "the successor self-edge must be delete-only"

    def test_supersede_preview_agrees_when_a_same_type_repoint_target_exists(
            self, sdk):
        """The SECOND precondition of the divergence: a direct IMPL/NAND
        self-loop is NECESSARY but not SUFFICIENT — and the suppressor is
        strictly SAME-TYPE.

        The writer's out-pass repoints `(old)-[:T]->(old)` to `(new)->(old)`
        with a MERGE **typed by the self-loop's own type**
        (`MERGE (new)-[nr:{rtype}]->(t)`), so the divergence requires no edge
        `(new)-[:T]->(old)` of THAT type to already exist. When one does, the
        MERGE collapses onto it, the in-pass delete-onlys that PRE-EXISTING
        edge — which this preview also counts, under `edges_dropped`, because
        its far endpoint is the successor — and the two totals agree.
        Measured: preview 3, writer 3.

        Pins the fact behind the docstring's precondition, so a future author
        cannot quietly drop it back to "NOT equal when `old` carries a
        self-loop" without this test going red.
        """
        old, new = _seed_point(sdk, "old"), _seed_point(sdk, "new")
        x = _seed_point(sdk, "x")
        sdk._get_proj().g.query(
            "MATCH (a:Point {id:$o}) CREATE (a)-[:IMPL]->(a)", params={"o": old})
        sdk.create_direct_edge("IMPL", old, x)
        # THE precondition: the repoint target already exists.
        sdk.create_direct_edge("IMPL", new, old)

        from tortoise.mcp_server import _preview_supersede

        preview = _preview_supersede(sdk, old, new)
        real = sdk.supersede(old, new)

        assert preview["edges_transferred_from_old"] == real["edges_transferred"], (
            "a pre-existing repoint target makes the writer's out-pass MERGE a "
            "no-op, so its double-booking does not occur and the two agree",
            preview["edges_transferred_from_old"], real["edges_transferred"])

    def test_supersede_preview_still_diverges_on_an_opposite_type_edge(
            self, sdk):
        """The suppressor is ONLY a same-type edge — an opposite-type
        pre-existing edge leaves the divergence intact.

        The out-pass MERGE is typed by the self-loop's own rel type
        (`MERGE (new)-[nr:{rtype}]->(t)`), so `(new)-[:NAND]->(old)` cannot
        collapse an `(old)-[:IMPL]->(old)` repoint. The out-pass still
        creates `(new)-[:IMPL]->(old)`, and the in-pass — which matches
        `IMPL|NAND` — takes both it and the pre-existing NAND edge
        delete-only. Writer 4, preview 3.

        Without this leg the docstring can over-claim the condition as
        "`(new)-[:IMPL|NAND]->(old)` does not already exist" and stay green.
        """
        old, new = _seed_point(sdk, "old"), _seed_point(sdk, "new")
        x = _seed_point(sdk, "x")
        sdk._get_proj().g.query(
            "MATCH (a:Point {id:$o}) CREATE (a)-[:IMPL]->(a)", params={"o": old})
        sdk.create_direct_edge("IMPL", old, x)
        # The OPPOSITE type — must NOT suppress the divergence.
        sdk.create_direct_edge("NAND", new, old)

        from tortoise.mcp_server import _preview_supersede

        preview = _preview_supersede(sdk, old, new)
        real = sdk.supersede(old, new)

        p, w = preview["edges_transferred_from_old"], real["edges_transferred"]
        assert p == 3, ("preview: self-loop dropped + old->x merged + the "
                        "pre-existing NAND edge dropped", p)
        assert w == 4, ("writer: out self-loop + out old->x + in NAND + in the "
                        "freshly created IMPL", w)
        assert w > p, ("the opposite-type edge cannot collapse a MERGE typed by "
                       "the self-loop's own type, so the writer still "
                       "double-books", p, w)

    def test_supersede_preview_is_the_accurate_count_for_a_direct_self_loop(
            self, sdk):
        """The one graph where the preview and the writer DISAGREE, and the
        preview is the one that is right.

        A direct `(old)-[:IMPL]->(old)` self-loop is removed by the write, so
        it is one edge — but the writer books it twice. Its out-pass repoints
        `(old)-[:IMPL]->(old)` to `(new)->(old)` (`transferred += 1`), then its
        in-pass matches the edge it just created and takes the
        far-endpoint-is-the-successor delete-only branch (`transferred += 1`
        again). The preview dedups the two passes (`seen_direct`) and counts
        the edge once. This pins the exact, documented drift so nobody
        "fixes" the preview toward the writer's inflated number.

        The self-loop is minted by Cypher, not by `create_direct_edge` — that
        API refuses `source_id == target_id`. The graph can still carry one
        (raw Cypher / an import), which is why the preview has the branch.
        """
        old, new = _seed_point(sdk, "old"), _seed_point(sdk, "new")
        x = _seed_point(sdk, "x")
        sdk._get_proj().g.query(
            "MATCH (a:Point {id:$o}) CREATE (a)-[:IMPL]->(a)",
            params={"o": old})
        sdk.create_direct_edge("IMPL", old, x)  # one ordinary transferred edge

        from tortoise.mcp_server import _preview_supersede

        before_at_old = _edge_keys_at(sdk, old)
        preview = _preview_supersede(sdk, old, new)
        real = sdk.supersede(old, new)
        after_at_old = _edge_keys_at(sdk, old)

        # GROUND TRUTH: two edges were incident to `old` (the self-loop and the
        # ordinary IMPL out), and the write removed both — nothing remains.
        assert len(before_at_old) == 2, before_at_old
        assert len(after_at_old) == 0, after_at_old
        # The preview reports exactly that. The writer's own counter is the
        # one that is wrong (it books the self-loop in both 2a-DIRECT passes),
        # so pin the DIRECTION of the drift — the writer over-counts — rather
        # than its exact inflated value: a writer-side fix must not redden a
        # test about the preview.
        assert preview["edges_transferred_from_old"] == 2, preview
        assert real["edges_transferred"] > preview["edges_transferred_from_old"], real
        dropped = [d for d in preview["edges_dropped"] if d["other"] == old]
        assert dropped, preview["edges_dropped"]
        assert "self-loop" in dropped[0]["reason"], dropped

    def test_supersede_preview_reports_the_edges_remaining_at_old(self, sdk):
        """The transfer set is NOT the whole blast radius at `old`: an edge the
        writer does not own (`related`) stays there. The preview must report
        that residual, and the count must match what the real write leaves —
        a `edges_transferred_from_old` alone reads as if every edge at `old`
        moved."""
        old, new = _seed_point(sdk, "old"), _seed_point(sdk, "new")
        x, y = _seed_point(sdk, "x"), _seed_point(sdk, "y")
        subj = sdk.create_entity(
            "subject", "S", subjectKind="company")["node"]["id"]
        sdk.create_direct_edge("IMPL", old, x)      # transferred
        sdk.create_edge("aboutSubject", old, subj)  # transferred
        sdk.create_edge("related", old, y)          # in NO transfer leg

        from tortoise.mcp_server import _preview_supersede

        preview = _preview_supersede(sdk, old, new)
        assert preview["edges_transferred_from_old"] == 2, preview
        assert preview["edges_remaining_at_old"] == 1, preview

        sdk.supersede(old, new)
        remaining = _edge_keys_at(sdk, old)  # excludes the CORRECTS edge
        assert len(remaining) == preview["edges_remaining_at_old"], remaining
        assert any(rtype == "related" for _, rtype, _, _ in remaining), remaining

    def test_supersede_preview_counts_a_duplicate_old_id(self, sdk):
        """The writer's status write is a bare `MATCH (n:Point {id}) SET …` —
        it stamps EVERY matching node, so `nodes_affected` must count them
        rather than hardcode 1 (which `_preview_invalidate` already gets
        right)."""
        proj = sdk._get_proj()
        new = _seed_point(sdk, "new")
        proj.g.query(
            "CREATE (a:Point {id:'olddup2', content:'a', "
            "pointKind:'statement'})")
        proj.g.query(
            "CREATE (b:Point {id:'olddup2', content:'b', "
            "pointKind:'statement'})")

        from tortoise.mcp_server import _preview_supersede

        preview = _preview_supersede(sdk, "olddup2", new)
        assert preview["nodes_affected"] == 2, preview

    def test_delete_entity_preview_uses_the_canonical_id_property_table(
            self, mcp, sdk, monkeypatch):
        """The label→id-property table is IMPORTED from
        `projection._CANONICAL_ENTITY_ID_PROPS`, never re-hardcoded. Patching
        it at its home is invisible to a local copy — so a preview that still
        counts the patched label proves the import (and, conversely, drift in
        the canonical table propagates instead of silently under-reporting)."""
        import tortoise.projection as projection

        sdk._get_proj().g.query(
            "CREATE (n:Widget {widgetId:'w-canon', content:'x'})")
        monkeypatch.setattr(projection, "_CANONICAL_ENTITY_ID_PROPS",
                            (("Widget", "widgetId"),))
        preview = mcp.tortoise_delete_entity("w-canon", dry_run=True)
        assert preview["nodes_removed"] == 1, preview
        assert preview["nodes"] == ["w-canon"], preview

    def test_supersede_structural_rels_are_the_writers_constant(self):
        """A re-hardcoded rel list here would drift out of the writer's 2b
        transfer silently; the preview must import the writer's own constant."""
        import tortoise.mcp_server as m
        from tortoise import sdk as sdk_mod

        assert (m.SUPERSEDE_STRUCTURAL_RELS
                is sdk_mod.SUPERSEDE_STRUCTURAL_RELS)


# ── D. the guards fire identically in dry-run ───────────────────────

class TestDryRunKeepsValidation:
    """A preview over an input the write would reject must reject it too —
    "would delete 1 node" over an operator is a lie in the other direction."""

    def test_retract_preview_rejects_an_operator(self, mcp, sdk):
        a, b = _seed_point(sdk, "a"), _seed_point(sdk, "b")
        op = sdk.create_operator("IMPL", a, [b])["id"]
        before = _graph_counts(sdk)
        result = mcp.tortoise_retract_point(op, dry_run=True)
        assert result.get("error"), result
        assert _graph_counts(sdk) == before

    def test_invalidate_preview_rejects_a_self_correction(self, mcp, sdk):
        a = _seed_point(sdk, "a")
        before = _graph_counts(sdk)
        result = mcp.tortoise_invalidate(a, a, dry_run=True)
        assert result.get("error"), result
        assert _graph_counts(sdk) == before

    def test_invalidate_preview_on_a_missing_point(self, mcp, sdk):
        b = _seed_point(sdk, "b")
        before = _graph_counts(sdk)
        result = mcp.tortoise_invalidate("no-such-id", b, dry_run=True)
        assert result["invalidated"] is False
        assert _graph_counts(sdk) == before

    def test_invalidate_preview_rejects_an_inverted_predecessor_window(
            self, mcp, sdk):
        """#5358 parity: the writer refuses a future-dated predecessor, so the
        preview must refuse it too — otherwise `dry_run=True` reports "would
        invalidate" for an operation the write raises on (the preview's own
        contract). The shared `_assert_window_start_not_inverted` is what
        makes the two agree; this is the differential that keeps it shared.
        """
        future = (_dt.datetime.now(_dt.UTC)
                  + _dt.timedelta(days=30)).replace(microsecond=0)
        old = sdk.create_point(
            "statement", "future claim", validFrom=future.isoformat())["id"]
        new = _seed_point(sdk, "replacement")
        before = _graph_counts(sdk)

        preview = mcp.tortoise_invalidate(old, new, dry_run=True)
        assert preview.get("error"), preview
        assert "retract_point" in str(preview["error"])
        # the write path refuses the SAME input, identically
        with pytest.raises(ValueError, match="retract_point"):
            sdk.invalidate_point(old, new)
        assert _graph_counts(sdk) == before

    def test_supersede_preview_transfer_false_rejects_an_inverted_window(
            self, mcp, sdk):
        """`tortoise_supersede(transfer_edges=False)` reuses the invalidate
        preview, so it inherits the #5358 refusal — pinned, because a
        re-divergence of the two previews would otherwise be silent."""
        future = (_dt.datetime.now(_dt.UTC)
                  + _dt.timedelta(days=30)).replace(microsecond=0)
        old = sdk.create_point(
            "statement", "future claim", validFrom=future.isoformat())["id"]
        new = _seed_point(sdk, "replacement")
        before = _graph_counts(sdk)
        result = mcp.tortoise_supersede(
            old, new, transfer_edges=False, dry_run=True)
        assert result.get("error"), result
        assert _graph_counts(sdk) == before


# ── E. the dry run is still auth-gated ──────────────────────────────

class TestDryRunIsAuthGated:
    def test_preview_is_wrapped_in_safe(self, mcp, sdk):
        """Transport mode unset = fail-closed. The preview must not become a
        way to read graph structure without passing _safe."""
        before = _graph_counts(sdk)
        token = _transport_mode.set(None)
        try:
            result = mcp.tortoise_delete_point("whatever", dry_run=True)
        finally:
            _transport_mode.reset(token)
        assert result.get("error"), result
        assert _graph_counts(sdk) == before


class TestDryRunIsMeteredAsARead:
    def test_a_write_tool_preview_counts_as_a_read(self, monkeypatch):
        """A dry run performs only reads, so it must not be invisible to the
        read-velocity counter just because the tool it previews is a write."""
        import tortoise.abuse as abuse_mod
        import tortoise.mcp_server as ms
        calls = []
        monkeypatch.setattr(
            abuse_mod, "record_read",
            lambda key_id, org_id, now=None: calls.append((key_id, org_id)))
        ms.maybe_record_mcp_read("tortoise_delete", "team-x", {"key_id": "k1"})
        assert calls == [], "a real delete is a write, never counted as a read"
        ms.maybe_record_mcp_read("tortoise_delete", "team-x",
                                 {"key_id": "k1"}, dry_run=True)
        assert calls == [("k1", "team-x")], "a dry run is a read"

    def _dispatch_reads(self, monkeypatch, name, arguments):
        """Drive the REAL dispatch seam and return the recorded reads."""
        import asyncio

        import tortoise.abuse as abuse_mod
        import tortoise.mcp_server as ms
        from tortoise.mcp_auth import _current_org_id, _current_org_limits

        calls = []
        monkeypatch.setattr(
            abuse_mod, "record_read",
            lambda key_id, org_id, now=None: calls.append((key_id, org_id)))

        async def _stub(*a, **k):
            return {"ok": True}

        monkeypatch.setattr(ms, "_await_under_mcp_wait_bound", _stub)
        monkeypatch.setattr(ms, "_enforce_mcp_tool_scope", lambda n: None)
        monkeypatch.setattr(ms, "_emit_mcp_tool_call_telemetry",
                            lambda *a, **k: None)
        tok_o = _current_org_id.set("team-x")
        tok_l = _current_org_limits.set({"key_id": "k1"})
        try:
            asyncio.run(ms._wrapped_call_tool(name, arguments))
        finally:
            _current_org_id.reset(tok_o)
            _current_org_limits.reset(tok_l)
        return calls

    def test_a_stray_dry_run_on_a_non_preview_tool_is_not_metered(
            self, monkeypatch):
        """`arguments` is the PRE-validation dict and this metering runs
        BEFORE the dispatch: a write tool that does not declare `dry_run`
        rejects the key and never performs the read. Recording one would count
        a call that did not happen."""
        assert self._dispatch_reads(
            monkeypatch, "tortoise_create_point", {"dry_run": True}) == []

    def test_a_declared_dry_run_is_still_metered_through_the_seam(
            self, monkeypatch):
        assert self._dispatch_reads(
            monkeypatch, "tortoise_delete", {"dry_run": True}
        ) == [("k1", "team-x")]


class TestPreviewToolSetIsDeclared:
    """`_dry_run_tool_names()` is the metering gate (#4057 review P2): the read
    counter must not be movable by a client-supplied argument naming a tool
    that does not declare it. Pin the derivation against the REAL signatures
    so the gate cannot go stale."""

    def test_the_six_preview_tools_declare_dry_run(self):
        import tortoise.mcp_server as m
        names = m._dry_run_tool_names()
        assert set(DESTRUCTIVE_TOOLS) <= names, sorted(names)

    def test_a_write_tool_without_dry_run_is_not_in_the_set(self):
        import tortoise.mcp_server as m
        assert "tortoise_create_point" not in m._dry_run_tool_names()

    def test_every_name_in_the_set_really_declares_dry_run(self):
        import tortoise.mcp_server as m
        for name in sorted(m._dry_run_tool_names()):
            assert "dry_run" in inspect.signature(
                getattr(m, name)).parameters, name


# ── #4021: the preview must not preview a write the writer refuses ────

class TestSupersedeWindowParity:
    """#4021 — ``_preview_supersede`` re-implements the writer's window
    resolution, so a predecessor window whose END would precede its own
    ``validFrom`` must be refused by BOTH. A preview that reports a clean
    blast radius for a write that then raises is the fail-open direction of
    the same defect: the caller reads "safe to apply" and gets an error (or,
    before the fix, silent corruption).

    Verdict parity is the contract; the MESSAGE is compared only where the
    successor's window start is deterministic (a dated successor). An undated
    successor resolves through the clock (``_now_iso()`` in the preview, the
    writer's own ``now``), so those two instants differ and only the verdict
    can be asserted.
    """

    def _pair(self, sdk, predecessor_start, successor_start=None):
        old = sdk.create_point("statement", "claim v1",
                               validFrom=predecessor_start)["id"]
        kw = {} if successor_start is None else {"validFrom": successor_start}
        new = sdk.create_point("statement", "claim v2", **kw)["id"]
        return old, new

    def test_supersede_preview_refuses_a_retroactive_successor(self, mcp, sdk):
        old, new = self._pair(sdk, "2026-06-10", "2026-06-01")
        before = _graph_counts(sdk)

        result = mcp.tortoise_supersede(old, new, dry_run=True)

        assert result.get("error"), result
        assert "inverted window" in result["error"], result
        assert _graph_counts(sdk) == before, "the refused preview wrote"

    def test_supersede_preview_verdict_matches_writer(self, mcp, sdk):
        """Differential on the MCP surface: both paths scrub through
        ``_scrub_error``, so equality here is the byte-level parity contract —
        a dated successor makes ``succ_vf`` deterministic."""
        old, new = self._pair(sdk, "2026-06-10", "2026-06-01")

        preview = mcp.tortoise_supersede(old, new, dry_run=True)
        applied = mcp.tortoise_supersede(old, new)

        assert preview.get("error"), preview
        assert applied.get("error"), applied
        assert preview["error"] == applied["error"], (
            "preview and writer disagree on the refusal")

    def test_supersede_preview_refuses_undated_successor_case(self, mcp, sdk):
        """An undated successor resolves its window through the clock, so the
        preview and the writer take ``now`` at different instants — the verdict
        is asserted, not the message."""
        old, new = self._pair(sdk, "2099-01-01", None)
        before = _graph_counts(sdk)

        result = mcp.tortoise_supersede(old, new, dry_run=True)

        assert result.get("error"), result
        assert "inverted window" in result["error"], result
        assert _graph_counts(sdk) == before, "the refused preview wrote"
