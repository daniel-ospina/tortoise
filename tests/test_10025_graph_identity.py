"""#10025 — a graph's identity is a fixed id; its name is a user-editable label.

The failure these tests prevent, measured in production before the change:

* `record: {armed: true, ok: true, write_failures: {}}` on the running app while
  **every write was refused** — the health counters are a DECLINE channel only,
  so an armed-and-refusing deployment and an armed-and-writing one read alike;
* **128 of 128** `api_keys` org-wide (`graph_id IS NULL`), and **1**
  `public.graphs` row across **80** organizations;
* the default graph was SYNTHETIC — `graph_metadata()` emitted the literal
  `"default"`, which is not an id the record's
  `(org_id, graph_id) -> public.graphs (org_id, id)` FK can name. So the two
  lanes answered the SAME org-wide credential differently:
  `_graph_namespace_for()` resolved it, `record_tenant_for()` refused it.

The owner's ruling (2026-10-26): *"a graph should have a fixed ID number,
separate from the graph name which is a user variable which should be
changeable and yeah users should be able to name all the graphs"*. So the tests
below pin the three things that follow from it: the tenant is named from a
control-plane ROW (never a name, never a constant), the sentinel `"default"` is
NOT retired under existing consumers, and a rename cannot move identity.
"""

from __future__ import annotations

import pytest

from tortoise.record_writer import record_tenant_for
from tortoise.supabase_control import (
    default_graph_id_for,
    delete_graph_row,
    graph_metadata,
    set_graph_name,
)

DEFAULT_ROW_ID = "g_0123456789abcdef"


class _FakeCP:
    """The narrow slice of the control-plane client these seams use."""

    def __init__(self, tables: dict[str, list[dict]] | None = None,
                 fail: dict[str, str] | None = None) -> None:
        self.tables = {k: [dict(r) for r in v]
                       for k, v in (tables or {}).items()}
        self.fail = fail or {}
        self.queries: list[tuple[str, str, object]] = []

    def query(self, table: str, method: str = "GET", filters=None,
              select=None, json_body=None, order=None):
        self.queries.append((table, method, filters))
        if table in self.fail:
            raise RuntimeError(self.fail[table])
        rows = self.tables.get(table, [])
        for col, op, val in (filters or []):
            if op == "eq":
                rows = [r for r in rows if r.get(col) == val]
            elif op == "neq":
                rows = [r for r in rows if r.get(col) != val]
            elif op == "is":
                rows = [r for r in rows if r.get(col) is val]
        if method == "GET":
            return [dict(r) for r in rows]
        if method == "DELETE":
            self.tables[table] = [r for r in self.tables.get(table, [])
                                  if r not in rows]
            return None
        if method == "PATCH":
            for r in rows:
                r.update(json_body or {})
            return None
        if method == "POST":
            self.tables.setdefault(table, []).append(dict(json_body or {}))
            return None
        return None


# ── 1. The resolver: a row, or a refusal — never a guess ────────────────────

def test_org_wide_credential_is_filed_under_the_default_graphs_id() -> None:
    """Indicator (b): an org-wide key reaches the record under a REAL id."""
    tenant = record_tenant_for(
        {"org_id": "org-1", "graph_id": None,
         "default_graph_id": DEFAULT_ROW_ID},
        control_plane_ids=True)
    assert tenant == ("org-1", DEFAULT_ROW_ID)


def test_graph_bound_key_still_names_its_own_graph() -> None:
    """The default must never displace a credential that named a graph."""
    tenant = record_tenant_for(
        {"org_id": "org-1", "graph_id": "g_bound",
         "default_graph_id": DEFAULT_ROW_ID},
        control_plane_ids=True)
    assert tenant == ("org-1", "g_bound")


@pytest.mark.parametrize("credential", [
    {"org_id": "org-1", "graph_id": None},                     # no row resolved
    {"org_id": "org-1", "graph_id": None, "default_graph_id": None},
    {"org_id": "org-1", "graph_id": None, "default_graph_id": ""},
    {"org_id": "org-1", "graph_id": None, "default_graph_id": 17},  # wrong type
    {"graph_id": None, "default_graph_id": DEFAULT_ROW_ID},    # no org
])
def test_an_unnameable_tenant_still_refuses(credential: dict) -> None:
    """Fail-closed: no usable row ⇒ None ⇒ `no_tenant`, never a guess."""
    assert record_tenant_for(credential, control_plane_ids=True) is None


def test_a_self_host_credential_cannot_name_a_control_plane_graph() -> None:
    """#9192 standing: the self-host registry lane's ids are not record ids."""
    assert record_tenant_for(
        {"org_id": "org-1", "graph_id": "g_1", "default_graph_id": DEFAULT_ROW_ID},
        control_plane_ids=False) is None


# ── 2. The default row's id is READ from the control plane ──────────────────

def test_default_graph_id_is_read_from_the_kind_default_row() -> None:
    cp = _FakeCP(tables={"graphs": [
        {"id": "g_custom", "org_id": "org-1", "kind": "custom",
         "status": "active"},
        {"id": DEFAULT_ROW_ID, "org_id": "org-1", "kind": "default",
         "status": "active"},
    ]})
    assert default_graph_id_for(cp, "org-1") == DEFAULT_ROW_ID


def test_a_tombstoned_default_row_is_not_a_tenant() -> None:
    """A soft-deleted default row must not become a record tenant."""
    cp = _FakeCP(tables={"graphs": [
        {"id": DEFAULT_ROW_ID, "org_id": "org-1", "kind": "default",
         "status": "deleted"},
    ]})
    assert default_graph_id_for(cp, "org-1") is None


def test_another_orgs_default_row_never_names_this_orgs_tenant() -> None:
    """The read is scoped by `org_id` — a peer's row must be invisible."""
    cp = _FakeCP(tables={"graphs": [
        {"id": "g_peer_org_default", "org_id": "org-2", "kind": "default",
         "status": "active"},
    ]})
    assert default_graph_id_for(cp, "org-1") is None
    assert default_graph_id_for(cp, "org-2") == "g_peer_org_default"


def test_default_graph_id_is_none_without_a_row_or_on_a_read_failure() -> None:
    """No row and an unreadable table are the SAME answer: refuse."""
    assert default_graph_id_for(_FakeCP(tables={"graphs": []}), "org-1") is None
    assert default_graph_id_for(
        _FakeCP(fail={"graphs": "relation does not exist"}), "org-1") is None


# ── 3. The sentinel is not retired; the real id rides beside it ─────────────

def test_graph_metadata_keeps_the_sentinel_and_adds_the_stable_id() -> None:
    """`graph_id == "default"` is compared in 8+ places (delete guard, backup
    sweep, SDK, dashboard). Retiring it here would un-recognise the default
    graph in all of them, so the real id is ADDITIVE."""
    cp = _FakeCP(tables={
        "organizations": [{"id": "org-1", "graph_name": "team_org-1"}],
        "graphs": [{"id": DEFAULT_ROW_ID, "org_id": "org-1", "name": "default",
                    "kind": "default", "namespace": "team_org-1",
                    "status": "active", "recording": None}],
    })
    rows = graph_metadata(cp, "org-1")
    assert rows[0]["graph_id"] == "default"      # unchanged for every consumer
    assert rows[0]["stable_id"] == DEFAULT_ROW_ID  # the record may file here
    assert rows[0]["namespace"] == "team_org-1"    # never the display name


def test_a_custom_graphs_stable_id_is_its_row_id() -> None:
    cp = _FakeCP(tables={
        "organizations": [{"id": "org-1", "graph_name": "team_org-1"}],
        "graphs": [{"id": "g_custom", "org_id": "org-1", "name": "research",
                    "kind": "custom", "namespace": "team_org-1_g_custom",
                    "status": "active", "recording": None}],
    })
    custom = next(r for r in graph_metadata(cp, "org-1") if r["kind"] == "custom")
    assert custom["graph_id"] == "g_custom"
    assert custom["stable_id"] == "g_custom"


def test_a_tombstoned_row_is_not_the_metadata_stable_id() -> None:
    """The SAME predicate `default_graph_id_for` applies.

    `stable_id` is documented as "the only field a caller may file record rows
    under", and the record FK accepts a tombstoned id — so a tombstoned row
    offered here would be a contract the code did not keep. The two readers of
    "where is the default row" must give one answer.
    """
    cp = _FakeCP(tables={
        "organizations": [{"id": "org-1", "graph_name": "team_org-1"}],
        "graphs": [{"id": DEFAULT_ROW_ID, "org_id": "org-1", "name": "old",
                    "kind": "default", "namespace": "team_org-1",
                    "status": "deleted", "recording": None}],
    })
    rows = graph_metadata(cp, "org-1")
    assert rows[0]["stable_id"] is None
    assert rows[0]["name"] == "default"  # tombstoned display name not revived
    assert default_graph_id_for(cp, "org-1") is None  # one answer, not two


# ── 4. Rename is a label: identity and storage key never move ───────────────

def test_renaming_the_default_by_its_real_id_never_touches_the_namespace() -> None:
    """The branch is keyed on the row's KIND, not on the `"default"` sentinel —
    the default graph now has a real id, and a rename by that id must still
    take the default path (which refuses to write `namespace`)."""
    cp = _FakeCP(tables={
        "organizations": [{"id": "org-1", "graph_name": "team_org-1"}],
        "graphs": [{"id": DEFAULT_ROW_ID, "org_id": "org-1", "name": "main",
                    "kind": "default", "namespace": "team_org-1",
                    "status": "active"}],
    })
    assert set_graph_name(cp, "org-1", DEFAULT_ROW_ID, "renamed") is True
    row = cp.tables["graphs"][0]
    assert row["name"] == "renamed"
    assert row["id"] == DEFAULT_ROW_ID          # identity survives the rename
    assert row["namespace"] == "team_org-1"     # storage key untouched
    # ...and the record tenant is still the same graph after the rename.
    assert default_graph_id_for(cp, "org-1") == DEFAULT_ROW_ID


def test_renaming_a_custom_graph_patches_only_its_name() -> None:
    cp = _FakeCP(tables={
        "organizations": [{"id": "org-1", "graph_name": "team_org-1"}],
        "graphs": [{"id": "g_custom", "org_id": "org-1", "name": "old",
                    "kind": "custom", "namespace": "team_org-1_g_custom",
                    "status": "active"}],
    })
    assert set_graph_name(cp, "org-1", "g_custom", "new") is True
    row = cp.tables["graphs"][0]
    assert (row["name"], row["id"], row["namespace"]) == (
        "new", "g_custom", "team_org-1_g_custom")


# ── 5. The mint ROLLBACK cannot hard-delete the default row ─────────────────

def test_a_failed_mint_cannot_hard_delete_the_default_graph() -> None:
    """`delete_graph_row` is the mint rollback. The record's layer tables FK
    `(org_id, graph_id) -> public.graphs (org_id, id) ON DELETE CASCADE`, so a
    hard delete of the default row would take that org's record rows with it.
    Before #10025 "it has no row" kept this off the default graph; now an
    explicit `kind neq 'default'` filter must."""
    cp = _FakeCP(tables={"graphs": [
        {"id": DEFAULT_ROW_ID, "org_id": "org-1", "kind": "default",
         "status": "active"},
        {"id": "g_minted", "org_id": "org-1", "kind": "custom",
         "status": "active"},
    ]})
    delete_graph_row(cp, "org-1", DEFAULT_ROW_ID)
    assert sorted(r["id"] for r in cp.tables["graphs"]) == [
        DEFAULT_ROW_ID, "g_minted"]  # the default row was NOT touched
    # ...and the rollback still works on the row it exists for
    delete_graph_row(cp, "org-1", "g_minted")
    assert [r["id"] for r in cp.tables["graphs"]] == [DEFAULT_ROW_ID]
