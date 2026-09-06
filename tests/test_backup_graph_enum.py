"""Unit tests for backup_sweep.enumerate_team_graphs — the per-graph sweep
seam (#2313). Pure fakes, no DB: runs identically on the embedded and docker
lanes (never mints real graphs)."""

from __future__ import annotations

from tortoise.backup_sweep import enumerate_team_graphs


class _ResultSet:
    def __init__(self, result_set):
        self.result_set = result_set


class _FakeGraphsTable:
    """Supabase-dialect fake implementing the queries graph_metadata makes
    (teams row + graphs rows)."""

    def __init__(self, teams_row: dict, graphs_rows: list[dict]):
        self._teams = teams_row
        self._graphs = graphs_rows

    def query(self, table: str, *, select=None, filters=None, order=None):
        # graph_metadata query shapes:
        #   teams: select id,graph_name filters [("id","eq",team_id)]
        #   graphs: select recording filters [("team_id","eq",..),("kind","eq","default")]
        #   graphs: select [...] filters team/custom/active order created_at
        if table == "teams":
            return [self._teams]
        fl = {k: v for (k, op, v) in (filters or [])}
        if fl.get("kind") == "default":
            rows = [r for r in self._graphs if r["kind"] == "default"]
            return [{"recording": r.get("recording")} for r in rows]
        # custom + active query (order by created_at)
        rows = [r for r in self._graphs
                if r["kind"] == "custom" and r["status"] == "active"]
        return [dict(r) for r in rows]


class _FakeRegistry:
    """Registry-dialect fake: cypher query returns a result-set object."""

    def __init__(self, graph_nodes: list[dict]):
        self._nodes = graph_nodes

    def query(self, q, params=None):
        if "RETURN properties(g)" in q:
            return _ResultSet([(dict(p),) for p in self._nodes])
        raise AssertionError(f"unexpected cypher: {q}")


def _supabase_source(teams_row=None, graphs_rows=None):
    return _FakeGraphsTable(
        teams_row or {"id": "t1", "graph_name": "team_t1"},
        graphs_rows or [],
    )


def test_supabase_default_only():
    src = _supabase_source()
    out = enumerate_team_graphs(src, "t1")
    assert out == [{"graph_id": "default", "kind": "default",
                    "namespace": "team_t1"}]


def test_supabase_default_plus_customs_excludes_deleted_and_default_rows():
    src = _supabase_source(graphs_rows=[
        {"id": "g_a", "team_id": "t1", "name": "alpha", "kind": "custom",
         "namespace": "team_t1_g_a", "status": "active",
         "recording": None, "created_at": "2026-09-01T00:00:00Z"},
        {"id": "g_b", "team_id": "t1", "name": "beta", "kind": "custom",
         "namespace": "team_t1_g_b", "status": "active",
         "recording": None, "created_at": "2026-09-02T00:00:00Z"},
        {"id": "g_del", "team_id": "t1", "name": "gone", "kind": "custom",
         "namespace": "team_t1_g_del", "status": "deleted",
         "recording": None, "created_at": "2026-09-03T00:00:00Z"},
    ])
    out = enumerate_team_graphs(src, "t1")
    assert [g["graph_id"] for g in out] == ["default", "g_a", "g_b"]
    assert all(g["kind"] != "deleted" for g in out)


def test_registry_default_normalized_and_deleted_filtered():
    src = _FakeRegistry([
        {"id": "g_rand_default", "team_id": "t1", "name": "default",
         "kind": "default", "namespace": "team_t1", "status": "active"},
        {"id": "g_x", "team_id": "t1", "name": "x", "kind": "custom",
         "namespace": "team_t1_g_x", "status": "active"},
        {"id": "g_y", "team_id": "t1", "name": "y", "kind": "custom",
         "namespace": "team_t1_g_y", "status": "deleted"},
    ])
    out = enumerate_team_graphs(src, "t1")
    assert out[0]["graph_id"] == "default"  # normalized from random gid
    assert [g["graph_id"] for g in out] == ["default", "g_x"]
    assert out[1]["namespace"] == "team_t1_g_x"


def test_registry_pre_c1_nodes_default_to_active():
    # Pre-C1 Graph nodes lack status — they count as active (mode-agnostic
    # with the seam's coalesce contract).
    src = _FakeRegistry([
        {"id": "g_legacy", "team_id": "t1", "name": "legacy",
         "kind": "custom", "namespace": "team_t1_g_legacy"},
    ])
    out = enumerate_team_graphs(src, "t1")
    assert [g["graph_id"] for g in out] == ["g_legacy"]
