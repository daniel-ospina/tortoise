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

import time

import pytest

from tortoise import mcp_auth
from tortoise.record_writer import record_tenant_for
from tortoise.supabase_control import (
    default_graph_id_for,
    delete_graph_row,
    ensure_default_graph_row,
    graph_metadata,
    provision_org,
    provision_org_with_token,
    set_graph_name,
    set_graph_recording,
)

DEFAULT_ROW_ID = "g_0123456789abcdef"


class _FakeCP:
    """The narrow slice of the control-plane client these seams use.

    ``fail`` is keyed by TABLE (every method fails) or by ``(table, method)``
    (only that verb fails) — the narrow arm exists so a test can exercise a
    failure in the INSERT while the reads that guard it succeed (#10025 review
    F4: a table-wide failure was satisfied by `ensure_default_graph_row`'s
    first read, so the insert the test named never ran).
    """

    def __init__(self, tables: dict[str, list[dict]] | None = None,
                 fail: dict[object, str] | None = None) -> None:
        self.tables = {k: [dict(r) for r in v]
                       for k, v in (tables or {}).items()}
        self.fail = fail or {}
        self.queries: list[tuple[str, str, object]] = []

    def query(self, table: str, method: str = "GET", filters=None,
              select=None, json_body=None, order=None):
        self.queries.append((table, method, filters))
        failure = self.fail.get((table, method))
        if failure is None:
            failure = self.fail.get(table)
        if failure is not None:
            raise RuntimeError(failure)
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
    # A PRESENT but unusable `graph_id` refuses — it must never fall through to
    # the org default, which would WIDEN a graph-bound credential into an
    # org-wide one (the invariant `_graph_namespace_for` states in words).
    # Pinned WITH a valid `default_graph_id` present, because that is the only
    # shape that can tell the two behaviours apart: without it, an
    # implementation that fell through would return ``None`` too and the pin
    # would be silent.
    {"org_id": "org-1", "graph_id": "", "default_graph_id": DEFAULT_ROW_ID},
    {"org_id": "org-1", "graph_id": 17, "default_graph_id": DEFAULT_ROW_ID},
    # The `"default"` SENTINEL is not a `public.graphs.id` (`g_<16hex>`), so
    # filing under it would produce a row the record's FK rejects. The shape is
    # reachable in production: the org-list payload's `default_graph_id` carries
    # exactly this sentinel (`hosted_api`'s graphs listing), so the refusal here
    # is load-bearing rather than theoretical.
    {"org_id": "org-1", "graph_id": None, "default_graph_id": "default"},
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


# ── 3b. The id a listed graph carries is usable on the WRITE path ──────────


def test_an_id_read_off_the_listing_drives_a_write_through_the_resolver() -> None:
    """One surface's id must be another surface's input.

    `graph_metadata` is what `GET /v1/graphs` and `list_memory_graphs` return;
    `resolve_graph_target` is the resolver every MCP write passes through. A
    dual-surface caller (list over REST, write over MCP — the shape this repo's
    own agents take) was handed a graph's `stable_id` and REFUSED by the write
    path, while the `"default"` SENTINEL — explicitly not an id — resolved. For
    the default row the sentinel occupies `graph_id`, so `stable_id` is the only
    field that can name it by id at all; that is the defect this pins.
    """
    cp = _FakeCP(tables={
        "organizations": [{"id": "org-1", "graph_name": "team_org-1"}],
        "graphs": [
            {"id": DEFAULT_ROW_ID, "org_id": "org-1", "name": "default",
             "kind": "default", "namespace": "team_org-1",
             "status": "active", "recording": None},
            {"id": "g_custom", "org_id": "org-1", "name": "research",
             "kind": "custom", "namespace": "team_org-1_g_custom",
             "status": "active", "recording": None},
        ],
    })
    rows = graph_metadata(cp, "org-1")
    for listed in rows:
        resolved = mcp_auth.resolve_graph_target(
            "org-1", listed["stable_id"], rows=rows)
        assert resolved["stable_id"] == listed["stable_id"]
        assert resolved["graph_id"] == listed["graph_id"]
    # The sentinel and the default row's REAL id name the same row — and the
    # default stays the untouched path, so passing the id is byte-identical to
    # passing the sentinel (and to omitting the argument).
    by_id = mcp_auth.resolve_graph_target("org-1", DEFAULT_ROW_ID, rows=rows)
    assert by_id == mcp_auth.resolve_graph_target(
        "org-1", "default", rows=rows) == rows[0]
    assert by_id["kind"] == "default"
    assert mcp_auth.use_graph_target(by_id) is by_id  # sets no ContextVar


def test_an_unreachable_id_still_refuses_and_names_the_reachable_set() -> None:
    """Accepting `stable_id` must not WIDEN reachability.

    The new comparison runs over the SAME filtered `reachable` list, so a
    foreign row's id (a leaky reader) and a soft-deleted row's id are both
    absent from it: refused, with the reachable set named and nothing confirmed
    about another tenant. This is the fail-closed property the resolver is
    trusted for.
    """
    rows = [
        {"graph_id": "default", "stable_id": DEFAULT_ROW_ID,
         "org_id": "org-1", "name": "default", "kind": "default",
         "namespace": "team_org-1", "status": "active"},
        {"graph_id": "g_foreign", "stable_id": "g_foreign_stable",
         "org_id": "org-2", "name": "Foreign", "kind": "custom",
         "namespace": "team_org-2_x", "status": "active"},
        {"graph_id": "g_gone", "stable_id": "g_gone", "org_id": "org-1",
         "name": "Gone", "kind": "custom",
         "namespace": "team_org-1_g_gone", "status": "deleted"},
    ]
    for unreachable in ("g_foreign_stable", "g_gone", "g_never_was"):
        with pytest.raises(mcp_auth.GraphTargetError) as excinfo:
            mcp_auth.resolve_graph_target("org-1", unreachable, rows=rows)
        message = str(excinfo.value)
        assert f"No graph {unreachable!r} is reachable" in message, message
        assert "team_org-2_x" not in message, message  # not confirmed to exist
        assert "default" in message, message            # the reachable set


def test_a_refusal_names_the_id_the_caller_could_actually_pass() -> None:
    """The hint is the only thing that tells a caller what to pass instead.

    Every refusal routes its reachable set through `_describe_graph`, so for the
    DEFAULT row that hint has to name the `stable_id` — the only real id the row
    has — and not just the `"default"` SENTINEL occupying `graph_id`. The row is
    renamed here so its own name carries no id hint either. Fails pre-fix, where
    the hint read `'Team HQ' ('default')`: the sentinel in the id slot, and the
    id the caller was holding nowhere in the message.
    """
    rows = [
        {"graph_id": "default", "stable_id": DEFAULT_ROW_ID,
         "org_id": "org-1", "name": "Team HQ", "kind": "default",
         "namespace": "team_org-1", "status": "active"},
        {"graph_id": "g_custom", "stable_id": "g_custom",
         "org_id": "org-1", "name": "research", "kind": "custom",
         "namespace": "team_org-1_g_custom", "status": "active"},
    ]
    with pytest.raises(mcp_auth.GraphTargetError) as excinfo:
        mcp_auth.resolve_graph_target("org-1", "g_typo", rows=rows)
    message = str(excinfo.value)
    assert "Team HQ" in message, message            # reachable, by its label
    assert DEFAULT_ROW_ID in message, message       # and by its real id
    assert f"id {DEFAULT_ROW_ID}" in message, message
    # A custom row's two ids are the SAME string, so it keeps the short form.
    assert "'research' (g_custom)" in message, message


# ── 4. Rename is a label: identity and storage key never move ───────────────

def test_renaming_the_default_by_its_real_id_never_touches_the_namespace() -> None:
    """The branch is keyed on the row's KIND, not on the `"default"` sentinel —
    the default graph now has a real id, and a rename by that id must still
    take the default path (which refuses to write `namespace`).

    ⚠️ SCOPE, stated so this is not read as more than it is: on a SINGLE default
    row this is behaviourally identical to the sentinel-keyed pre-change body,
    so this test does NOT discriminate the `kind` branch on its own. What the
    branch actually changes is the DUPLICATE-row case — a tombstoned default
    row beside an active one — and that is pinned by
    `test_renaming_the_default_by_its_id_never_patches_a_tombstone`.
    """
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


# ── 5b. A rename targets the ACTIVE default row, never a re-resolved `rows[0]` ─
#
# A tombstoned `kind='default'` row and an active one CAN coexist — the partial
# unique index is on `(org_id, name) WHERE status <> 'deleted'`, so the index
# does not forbid it and `20261028000001`'s `status = 'active'` guard mints a
# second row when the only row it can see is not active. ⚠️ No SUPPORTED path
# produces the tombstone: `soft_delete_graph` refuses `kind='default'`, which is
# why the migration treats the shape as defensive rather than normal. It is
# reachable by raw SQL / ops, and that is enough: PostgreSQL returns the two in
# unspecified order, so a status-blind re-read that PATCHes `rows[0]` can write
# the row the caller did not resolve and still return True — the API reports
# success and the visible rename never happens.

def _tombstoned_plus_active_default() -> _FakeCP:
    return _FakeCP(tables={
        "organizations": [{"id": "org-1", "graph_name": "team_org-1"}],
        "graphs": [
            # insertion order matters: this row is `rows[0]` for a status-blind
            # read, which is exactly the row the caller did NOT name.
            {"id": "g_tombstone", "org_id": "org-1", "name": "old",
             "kind": "default", "namespace": "team_org-1",
             "status": "deleted", "recording": True},
            {"id": DEFAULT_ROW_ID, "org_id": "org-1", "name": "main",
             "kind": "default", "namespace": "team_org-1",
             "status": "active", "recording": None},
        ],
    })


def test_renaming_the_default_by_the_sentinel_never_patches_a_tombstone() -> None:
    """RED ON THE PRE-FIX CODE.

    The sentinel `"default"` is not an id, so this is the ONE path that must
    resolve the row — and the pre-fix resolution re-read
    `(org_id, kind='default')` with NO status filter and PATCHed `rows[0]`,
    which the fixture puts the TOMBSTONE in. So the pre-fix code renamed a row
    nobody resolved, returned True, and left the visible (active) default still
    named `main`. ⚠️ Driving this by the row's REAL id does NOT discriminate:
    that path queries by `(id, org, status='active')` on both sides, so a
    real-id rename is byte-identical before and after the change (pinned by
    `test_renaming_the_default_by_its_real_id_never_touches_the_namespace`,
    which is behaviour coverage, not a regression pin).

    MEASURED (#10025 review F2, re-verified in this lane): both arms driven
    against three baselines — `main`'s `set_graph_name` ⇒ sentinel FAILED,
    real-id PASSED; the PR's first commit `9bbbe6397` (which introduced the
    `kind` branch but left the sentinel re-read status-blind) ⇒ both FAILED;
    this head ⇒ both PASSED. So THIS arm is the regression pin and the real-id
    arm is not.
    """
    cp = _tombstoned_plus_active_default()
    assert set_graph_name(cp, "org-1", "default", "renamed") is True
    rows = {r["id"]: r for r in cp.tables["graphs"]}
    assert rows[DEFAULT_ROW_ID]["name"] == "renamed"      # the ACTIVE row
    assert rows["g_tombstone"]["name"] == "old"           # untouched
    assert rows[DEFAULT_ROW_ID]["namespace"] == "team_org-1"


def test_renaming_the_default_by_its_id_never_patches_a_tombstone() -> None:
    """The REAL-id arm of the same fixture: the caller named a row, so the
    PATCH targets that id and the tombstone is untouched.

    Behaviour coverage, NOT a regression pin — see the sentinel test above for
    the arm that actually reds pre-fix. Measured: this arm PASSES on `main`'s
    pre-fix `set_graph_name` (the real-id path queried by
    `(id, org, status='active')` and PATCHed that id on both sides), so it
    cannot distinguish the fix. It still reds on `9bbbe6397`, where the new
    `kind` branch fell through to the status-blind sentinel re-read.
    """
    cp = _tombstoned_plus_active_default()
    assert set_graph_name(cp, "org-1", DEFAULT_ROW_ID, "renamed") is True
    rows = {r["id"]: r for r in cp.tables["graphs"]}
    assert rows[DEFAULT_ROW_ID]["name"] == "renamed"      # the named row
    assert rows["g_tombstone"]["name"] == "old"           # untouched
    assert rows[DEFAULT_ROW_ID]["namespace"] == "team_org-1"


def test_a_recording_override_targets_the_active_default_row() -> None:
    """`set_graph_recording`'s default path had the identical status-blind
    re-read (pre-existing code the migration made reachable)."""
    cp = _tombstoned_plus_active_default()
    assert set_graph_recording(cp, "org-1", "default", True) is True
    rows = {r["id"]: r for r in cp.tables["graphs"]}
    assert rows[DEFAULT_ROW_ID]["recording"] is True
    assert rows["g_tombstone"]["recording"] is True  # its own value, not ours
    assert rows[DEFAULT_ROW_ID]["name"] == "main"    # no name written


# ── 6. The FORWARD half: an organization created AFTER the migration ────────
#
# `20261028000001` backfills the organizations that exist when it runs. Nothing
# re-runs it, and the pre-fix code minted that row on no other path — so an
# organization created afterwards came out with NO `kind='default'` row,
# `default_graph_id_for` returned None for it, and `record_tenant_for` refused
# every record write `no_tenant` for ever while `/health` read armed and green.
# That is the measured incident #10025 exists to close, one per signup.

def _provision_params(org_id: str) -> dict:
    # A REAL uuid: `org_memberships.user_id` is a uuid column and the fake
    # enforces PostgREST's 22P02, so a placeholder id would fail the provision
    # for a reason that has nothing to do with the default row.
    return {
        "p_user_id": "9f1c0a62-6a4e-4a1f-9c2f-1b3d5e7f9a01", "p_identity": None,
        "p_org_id": org_id, "p_org_name": "Acme",
        "p_api_key": None, "p_key_hash": None, "p_lookup_hash": None,
        "p_key_prefix": None,
        "p_graph_name": f"org_{org_id}", "p_tier": "free",
    }


def _default_rows(cp) -> list[dict]:
    return [g for g in cp.tables.get("graphs", []) if g.get("kind") == "default"]


def test_provisioning_mints_the_default_row_a_new_org_needs() -> None:
    """RED ON THE PRE-FIX CODE: no graphs row was written on any creation path.

    The tenant must be nameable from the row the mint wrote — the assertion is
    the two answers agreeing, not merely "a row exists".
    """
    from tests.fake_control_plane import FakeControlPlane
    fake = FakeControlPlane()
    provision_org(fake, **_provision_params("org-new"))

    minted = _default_rows(fake)
    assert len(minted) == 1, f"expected exactly one default row, got {minted}"
    row = minted[0]
    assert row["status"] == "active"                 # what the reader keys on
    assert row["namespace"] == "org_org-new"         # the storage key, not a name
    assert row["name"] == "default" and row["org_id"] == "org-new"
    assert row["id"].startswith("g_") and len(row["id"]) == 18

    assert default_graph_id_for(fake, "org-new") == row["id"]
    assert record_tenant_for(
        {"org_id": "org-new", "graph_id": None, "default_graph_id": row["id"]},
        control_plane_ids=True) == ("org-new", row["id"])


def test_signup_provisioning_mints_the_same_row() -> None:
    """The signup wrapper is a DIFFERENT RPC (`provision_team_with_token`) —
    covering only the 15-arg one would leave every agent signup unnamed."""
    from tests.fake_control_plane import FakeControlPlane
    fake = FakeControlPlane()
    provision_org_with_token(fake, **_provision_params("org-signup"),
                             p_signup_token_hash="sha256:token-hash")

    minted = _default_rows(fake)
    assert len(minted) == 1, f"expected exactly one default row, got {minted}"
    assert default_graph_id_for(fake, "org-signup") == minted[0]["id"]


def test_the_mint_is_idempotent_and_never_moves_an_existing_row() -> None:
    """A replay/retry must return the SAME id — never a second row (which would
    also violate `uq_graphs_org_name_active` on the name 'default')."""
    cp = _FakeCP(tables={"organizations": [
        {"id": "org-1", "graph_name": "team_org-1"}]})
    first = ensure_default_graph_row(cp, "org-1", "team_org-1")
    second = ensure_default_graph_row(cp, "org-1", "team_org-1")
    assert first == second and first is not None
    assert len(_default_rows(cp)) == 1


def test_the_mint_does_not_fight_a_graph_already_named_default() -> None:
    """`uq_graphs_org_name_active` is the backstop: an org that already owns a
    graph NAMED 'default' gets no second row (the documented residual), rather
    than a unique violation that would fail provisioning outright."""
    cp = _FakeCP(tables={
        "organizations": [{"id": "org-1", "graph_name": "team_org-1"}],
        "graphs": [{"id": "g_taken", "org_id": "org-1", "name": "default",
                    "kind": "custom", "namespace": "team_org-1_g_taken",
                    "status": "active"}],
    })
    assert ensure_default_graph_row(cp, "org-1", "team_org-1") is None
    assert _default_rows(cp) == []


def test_a_failed_mint_raises_rather_than_claiming_success() -> None:
    """`insert_graph`'s contract, kept: a mint must not claim success for an
    unpersisted graph. A swallowed failure here IS the silent `no_tenant`
    state — an org nobody can name, with nothing in any log to say so.

    ⚠️ The failure is keyed on the POST specifically, so the two reads that
    guard the insert SUCCEED and the insert is genuinely reached — a
    table-wide `fail` would be satisfied by the first read and would stay green
    against an `ensure_default_graph_row` that caught and swallowed only the
    insert (#10025 review F4).
    """
    cp = _FakeCP(tables={"organizations": [
        {"id": "org-1", "graph_name": "team_org-1"}]},
        fail={("graphs", "POST"): "insert failed"})
    with pytest.raises(RuntimeError):
        ensure_default_graph_row(cp, "org-1", "team_org-1")
    # The witness that the insert was reached, not just the guard read.
    assert ("graphs", "POST", None) in cp.queries, (
        f"the insert was never attempted: {cp.queries}")
    assert not cp.tables.get("graphs"), "a failed mint wrote a row anyway"


def test_the_provisioning_wrappers_survive_a_failed_mint() -> None:
    """#10025 review F1: the mint is BEST-EFFORT at the provisioning seams.

    Their RPC has already committed, and every caller reads a raise as "nothing
    was provisioned" and compensates destructively (`/v1/register` deletes the
    data-plane graph, agent signup loses the only copy of the plaintext key,
    and the retry is then refused). So a failed mint must NOT raise out of
    them — the org is provisioned, the row is logged missing, and the org's
    next API-key request self-heals it.

    Driven through the REAL wrappers with a control plane whose `graphs` table
    is unreadable, so the raise would come from the mint and nowhere else — and
    asserted to have REACHED the mint (the retry window is the only observable
    that proves it), because "did not raise" is equally satisfied by a wrapper
    that never calls it.
    """
    from tests.fake_control_plane import FakeControlPlane
    from tortoise import supabase_control as sc

    class _NoGraphsCP(FakeControlPlane):
        """The provision RPC works; the `graphs` table does not."""

        def query(self, table, *a, **kw):  # type: ignore[override]
            if table == "graphs":
                raise RuntimeError("graphs unreadable")
            return super().query(table, *a, **kw)

    for wrapper in (provision_org, provision_org_with_token):
        cp = _NoGraphsCP()
        org_id = f"org-f1-{wrapper.__name__}"
        extra = {"p_signup_token_hash": "sha256:token-hash"} \
            if wrapper is provision_org_with_token else {}
        # Must NOT raise: the org is provisioned, only the default row is not.
        # A DISTINCT org per wrapper, deliberately: `_DEFAULT_ROW_MINT_RETRY` is
        # process state, so re-using one org id would let the first iteration's
        # recorded failure answer "not re-attempting" for the second — the
        # signup arm would then never reach the mint and could not distinguish
        # "routes through the best-effort helper" from "skipped for any
        # reason", which is exactly what this test claims to prove.
        wrapper(cp, **_provision_params(org_id), **extra)
        assert default_graph_id_for(cp, org_id) is None, (
            "the unreadable table answered a tenant")
        # ⚠️ The "did not raise" half above is satisfied by a wrapper that never
        # calls the mint at all — measured: deleting the call from
        # `provision_org_with_token` leaves it green. This is the assertion that
        # makes the arm falsifiable: an org reaches the retry window ONLY by
        # `_mint_default_graph_row_best_effort` having RUN and recorded its
        # failure. (No `clear()` per iteration: distinct orgs are distinct keys,
        # and conftest's autouse fixture resets the window per test.)
        assert org_id in sc._DEFAULT_ROW_MINT_RETRY, (
            f"{wrapper.__name__} never reached the mint — the best-effort "
            "route this test claims to prove is not exercised")


def test_a_missed_mint_is_healed_by_the_next_api_key_resolution() -> None:
    """#10025 review F1: the self-heal, so the unnamed state is not PERMANENT.

    Without it a missed mint is forever: `20261028000001` only backfills orgs
    that existed when it ran, and a retry of the provisioning request 409s
    (`uq_member_identity_active`). Here the org has an org-wide key and NO
    `kind='default'` row — the resolver must mint the row and name the
    credential with it in the SAME resolution. Driven through the real
    `resolve_api_key`, so deleting the self-heal call reds this.
    """
    from tests.fake_control_plane import FakeControlPlane
    from tortoise.auth import lookup_hash
    from tortoise.supabase_control import (
        _DEFAULT_ROW_MINT_RETRY,
        resolve_api_key,
    )

    _DEFAULT_ROW_MINT_RETRY.clear()   # module state; do not depend on test order
    key = "tt_selfheal0123456789"
    cp = FakeControlPlane({
        "organizations": [{"id": "org-1", "name": "One", "tier": "free",
                           "graph_name": "org_org-1"}],
        "api_keys": [],
        "graphs": [],
    })
    cp.seed("api_keys", [{
        "id": "key-1", "org_id": "org-1", "lookup_hash": lookup_hash(key),
        "key_prefix": key[:10], "created_via": "bootstrap",
        "created_by": "user-1", "expires_at": None, "revoked_at": None,
    }])

    resolved = resolve_api_key(cp, key)

    minted = _default_rows(cp)
    assert len(minted) == 1, f"the self-heal minted {len(minted)} rows"
    assert minted[0]["namespace"] == "org_org-1"
    assert resolved["default_graph_id"] == minted[0]["id"]
    assert resolved["graph_id"] is None
    # Idempotent: the next request re-reads the row and mints nothing new.
    assert resolve_api_key(cp, key)["default_graph_id"] == minted[0]["id"]
    assert len(_default_rows(cp)) == 1


def test_an_unreadable_control_plane_still_refuses_a_tenant() -> None:
    """The self-heal's failure direction: a mint it cannot perform leaves the
    credential UNNAMED (`no_tenant`), never filed under a guess — and it never
    fails the auth that carried it."""
    from tests.fake_control_plane import FakeControlPlane
    from tortoise.auth import lookup_hash
    from tortoise.supabase_control import (
        _DEFAULT_ROW_MINT_RETRY,
        resolve_api_key,
    )

    _DEFAULT_ROW_MINT_RETRY.clear()   # module state; do not depend on test order

    class _NoGraphsCP(FakeControlPlane):
        def query(self, table, *a, **kw):  # type: ignore[override]
            if table == "graphs":
                raise RuntimeError("graphs unreadable")
            return super().query(table, *a, **kw)

    key = "tt_selfheal0123456789"
    cp = _NoGraphsCP({
        "organizations": [{"id": "org-1", "name": "One", "tier": "free",
                           "graph_name": "org_org-1"}],
        "api_keys": [],
        "graphs": [],
    })
    cp.seed("api_keys", [{
        "id": "key-1", "org_id": "org-1", "lookup_hash": lookup_hash(key),
        "key_prefix": key[:10], "created_via": "bootstrap",
        "created_by": "user-1", "expires_at": None, "revoked_at": None,
    }])

    resolved = resolve_api_key(cp, key)   # must not raise
    assert resolved["default_graph_id"] is None
    assert record_tenant_for(
        resolved, control_plane_ids=True) is None


def test_a_mint_that_cannot_succeed_is_not_retried_on_every_request() -> None:
    """#10025 review round 13, F3: the self-heal is COST-BOUNDED, not per-request.

    Two of `ensure_default_graph_row`'s exits are permanent for the org (a live
    graph already named `default`; an empty `organizations.graph_name`) and a
    broken table is not fixed by the next request either. Without a retry window
    every authenticated request for such an org — and every production key is
    org-wide — pays 3 control-plane reads plus a WARNING (or an ERROR with a
    traceback) for ever, on the channel the #10025 diagnosis itself reads.

    ⚠️ The window must suppress only the WRITE attempt: the READ still runs, so a
    row that appears by another route (the migration re-run) is picked up at
    once. Both halves are asserted here.
    """
    from tests.fake_control_plane import FakeControlPlane
    from tortoise.auth import lookup_hash
    from tortoise.supabase_control import (
        _DEFAULT_ROW_MINT_RETRY,
        resolve_api_key,
    )

    _DEFAULT_ROW_MINT_RETRY.clear()
    key = "tt_selfheal0123456789"
    # No `graph_name` ⇒ the mint is a permanent no-op for this org.
    cp = FakeControlPlane({
        "organizations": [{"id": "org-1", "name": "One", "tier": "free"}],
        "api_keys": [], "graphs": [],
    })
    cp.seed("api_keys", [{
        "id": "key-1", "org_id": "org-1", "lookup_hash": lookup_hash(key),
        "key_prefix": key[:10], "created_via": "bootstrap",
        "created_by": "user-1", "expires_at": None, "revoked_at": None,
    }])

    resolve_api_key(cp, key)
    after_first = cp.query_count
    resolve_api_key(cp, key)
    # The second request must not re-pay the mint's guard reads.
    assert cp.query_count - after_first == 3, (
        f"the self-heal re-attempted on the second request "
        f"({cp.query_count - after_first} queries, expected the 3-read "
        f"resolution only)")

    # The READ is never suppressed: a row that appears by another route is
    # picked up on the very next request, while the window is still open.
    cp.seed("graphs", [{"id": "g_healed", "org_id": "org-1",
                        "name": "default", "kind": "default",
                        "status": "active", "namespace": "org_org-1"}])
    assert resolve_api_key(cp, key)["default_graph_id"] == "g_healed"


# ── The retry window under CONCURRENCY (#10025 review round 15, F1) ─────────
# `_DEFAULT_ROW_MINT_RETRY` is process-global, and this PR put it on three
# concurrent executors in ONE process: the auth pool (`resolve_api_key`'s
# self-heal), the org pool (the provisioning wrappers) and the event loop (the
# MCP lane). So its prune loop can run while a peer thread inserts a new org key
# — and PEP 3106: mutating a dict while an iterator over it is live "has an
# undefined effect and will in most cases raise a RuntimeError exception".


class _PeerInsertingTimestamp(float):
    """A stored timestamp that performs a PEER THREAD's insert on comparison.

    The prune loop evaluates `now - ts`, which is exactly the moment a
    concurrent `resolve_api_key`/`provision_org` lands its own org key, so doing
    the insert from `__rsub__` makes that race DETERMINISTIC instead of timed.
    `float.__rsub__` is consulted first for `now - ts` because this is a
    subclass of `float` and overrides the reflected operation.
    """

    def __new__(cls, value: float, on_compare):
        self = super().__new__(cls, value)
        self._on_compare = on_compare
        return self

    def __rsub__(self, other):
        self._on_compare()
        return float(other) - float(self)


def test_the_mint_retry_window_survives_a_concurrent_insert(monkeypatch) -> None:
    """#10025 review round 15, F1: the prune iterates a SNAPSHOT, never the view.

    Why it matters is the cast of the raise, not the prune: this helper is
    called from INSIDE `_mint_default_graph_row_best_effort`'s
    `except Exception:` arm and from the provisioning wrappers AFTER their RPC
    has committed, so a `RuntimeError` here escapes a function whose whole
    contract is "never fail the caller" — an auth 500 on the org-wide-key lane
    (the shape of all 128 production keys) and a costed account on the signup
    lane, whose caller compensates destructively on a raise.

    Seeded past the 1024-entry prune trigger, with the first stored timestamp's
    `__rsub__` standing in for the peer thread. The witness at the end proves
    that insert really does red the LIVE-view shape, so a pass here is the
    snapshot's and not luck.
    """
    from tortoise import supabase_control as sc

    now = 10_000.0
    live: dict[str, float] = {}
    live["org-expired"] = _PeerInsertingTimestamp(
        now - 10_000.0, lambda: live.setdefault("org-peer", now))
    for i in range(1030):          # > 1024 ⇒ the prune branch actually runs
        live[f"seed-{i}"] = now - 10_000.0
    monkeypatch.setattr(sc, "_DEFAULT_ROW_MINT_RETRY", live)

    sc._record_default_row_mint_failure("org-failing", now)   # must NOT raise

    assert "org-failing" in live, "the failure was not recorded at all"
    assert "org-peer" in live, (
        "the peer insert never happened — this test proved nothing")

    # Non-vacuity witness: on the LIVE view the helper used to iterate, that
    # same insert reds the iteration.
    witness: dict[str, float] = {}
    witness["org-expired"] = _PeerInsertingTimestamp(
        now - 10_000.0, lambda: witness.setdefault("org-peer", now))
    witness["seed-1"] = now - 10_000.0
    with pytest.raises(RuntimeError, match="changed size during iteration"):
        for _oid, ts in witness.items():          # the PRE-FIX shape
            _ = now - ts


def test_the_prune_snapshot_is_taken_under_the_cache_guard(
        monkeypatch) -> None:
    """The EXCLUSION half of the fix, so the two cannot drift apart.

    The test above pins the SNAPSHOT; this one pins EXCLUSION, so the two
    halves cannot drift apart. A snapshot alone does not exclude a peer thread
    — building it still walks the mapping, so a concurrent insert can raise
    inside the walk itself. This asserts the snapshot is taken while
    `_DEFAULT_ROW_MINT_RETRY_GUARD` is held, which is what stops a SECOND
    thread's record interleaving with this one's read-modify-write. Delete the
    `with` block and keep `copy()` — the plausible half-fix — and this goes
    red while the test above stays green (measured).

    Observed from inside the walk deliberately: the guard is process state, so
    the only honest witness is the state at the moment the snapshot is taken.
    """
    from tortoise import supabase_control as sc

    seen: dict[str, bool] = {}

    class _WatchesTheGuard(dict):
        def copy(self):  # type: ignore[override]
            seen["guard_locked"] = sc._DEFAULT_ROW_MINT_RETRY_GUARD.locked()
            return dict.copy(self)

    cache = _WatchesTheGuard()
    stale = time.monotonic() - sc._DEFAULT_ROW_MINT_RETRY_S - 1.0
    for n in range(sc._DEFAULT_ROW_MINT_RETRY_MAX + 1):
        dict.__setitem__(cache, f"org-stale-{n:04d}", stale)
    monkeypatch.setattr(sc, "_DEFAULT_ROW_MINT_RETRY", cache)

    sc._record_default_row_mint_failure("org-new", time.monotonic())

    assert seen.get("guard_locked") is True, (
        "the prune walk took its snapshot with the cache guard UNHELD — a "
        "concurrent record can interleave with the read-modify-write")


# ── The migration's two structural guarantees (#10025 review, at-head) ──────
# Both live in SCHEMA, so `supabase/tests/20261028000001_graphs_default_row_
# stable_id.sql` is where they are EXECUTED against real Postgres (PGlite). The
# two pins below are the complement that SQL cannot express: the suite replays
# the backfill itself, so it stays green if the MIGRATION FILE loses the clause
# it is replaying. Static by necessity, and honest about it — it asserts on the
# statement Postgres will actually run, with comments stripped so prose cannot
# satisfy it.

_MIGRATION = ("supabase/migrations/"
              "20261028000001_graphs_default_row_stable_id.sql")


def _migration_sql() -> str:
    """The migration's SQL with `--` comments removed (code only)."""
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / _MIGRATION
    assert path.exists(), f"the migration is missing: {path}"
    return "\n".join(
        line.split("--")[0] for line in path.read_text(encoding="utf-8").splitlines())


def test_the_migration_states_the_one_live_default_row_invariant() -> None:
    """Two LIVE `kind='default'` rows for one org are what makes every singular
    reader (`rows[0]`) ambiguous, and they were LEGAL: `uq_graphs_org_name_active`
    is on `(org_id, name)`, and `set_graph_name` inserts the user's chosen name
    while the backfill/mint insert 'default'. These two ordinary paths can commit
    concurrently and neither name-collides, so the pair is reachable — one org's
    record rows then split across two `graph_id`s, and `stable_id`/the displayed
    default name flicker per request. The index is the backstop; `status <>
    'deleted'` keeps the defensive tombstone-beside-active shape legal.
    """
    sql = _migration_sql()
    marker = "CREATE UNIQUE INDEX IF NOT EXISTS uq_graphs_org_default_active"
    assert marker in sql, (
        "the migration does not create the one-live-default-row backstop")
    index = sql.split(marker, 1)[1].split(";", 1)[0]
    assert "(org_id)" in index, f"the index is not keyed on the org: {index!r}"
    assert "kind = 'default'" in index, (
        f"the index is not scoped to default rows: {index!r}")
    assert "status <> 'deleted'" in index, (
        f"the index is not scoped to live rows: {index!r}")


def test_the_backfill_survives_a_concurrent_writer() -> None:
    """The backfill's guards are a READ-THEN-WRITE. `set_graph_recording`'s
    upsert and the auth/provisioning mint insert exactly `(org_id, name=
    'default', kind='default', status='active')` for exactly the orgs this
    statement targets, so one commit landing between its snapshot and its index
    insertion raises a unique violation — and Supabase applies a migration file
    in ONE transaction, so that aborts the backfill for EVERY org.

    A conflict here IS the row this migration wanted to exist, so it must be
    skipped rather than raised. Untargeted on purpose (Postgres: `DO NOTHING`
    without a `conflict_target` "conflicts with all usable constraints (and
    unique indexes) are handled") — a target would pin one index and let the
    other raise. The Python mint models the same race one layer up.
    """
    sql = _migration_sql()
    backfill = sql.split("INSERT INTO public.graphs", 1)[1].split(";", 1)[0]
    assert "ON CONFLICT DO NOTHING" in backfill, (
        "the backfill raises on a concurrent insert — a live writer's row "
        "would abort the whole migration transaction, for every org")
    assert "NOT EXISTS" in backfill, (
        "the backfill lost its existence guard — it is no longer idempotent")

# ── The convergent mint's adopt arm (#10025 review, at-head) ────────────────
# The guards above the insert are a read-then-write, so a concurrent winner
# makes the INSERT lose on a unique index; the adopt arm then re-reads and
# returns the winner. The winner need not be named 'default': `set_graph_name`'s
# insert arm mints `kind='default'` under the USER'S chosen name, so a rename
# committing inside that window leaves an ACTIVE default row called e.g.
# `Team HQ`. A `name = 'default'` filter on the adopt read misses it, re-raises,
# and logs "the org is unnamed" for an org that IS nameable — a false alarm on
# the one channel the #10025 diagnosis depended on.


class _WinnerLandsAfterTheGuards(_FakeCP):
    """The winner's row commits AFTER this mint's guards read, BEFORE its insert.

    Models exactly the interleaving the convergent arm exists for: `graphs` GETs
    answer from the pre-commit snapshot (empty) until an insert has been
    ATTEMPTED, and see the committed winner from then on.
    """

    def __init__(self, tables=None, fail=None) -> None:
        super().__init__(tables=tables, fail=fail)
        self.post_attempted = False

    def query(self, table, method="GET", filters=None, **kw):  # type: ignore[override]
        if table == "graphs" and method == "POST":
            self.post_attempted = True
        elif table == "graphs" and method == "GET" and not self.post_attempted:
            return []
        return super().query(table, method=method, filters=filters, **kw)


def test_the_adopt_arm_adopts_a_differently_named_winner() -> None:
    """A concurrent RENAME winner is adopted, not rejected.

    With a `name = 'default'` filter the adopt read returned zero rows here, so
    the mint raised and logged "the org's record writes stay refused `no_tenant`"
    for an org that `default_graph_id_for` names immediately. The read is org +
    kind + live-status (no name) — the same one `set_graph_name`'s convergent arm
    performs.
    """
    cp = _WinnerLandsAfterTheGuards(
        tables={"graphs": [{"id": "g_renamed", "org_id": "org-adopt",
                            "name": "Team HQ", "kind": "default",
                            "namespace": "org_org-adopt", "status": "active"}]},
        fail={("graphs", "POST"): 'duplicate key value violates unique '
                                 'constraint "uq_graphs_org_default_active"'})
    assert ensure_default_graph_row(cp, "org-adopt", "org_org-adopt") == "g_renamed"


def test_the_adopt_arm_does_not_adopt_a_tombstone() -> None:
    """…but a TOMBSTONED default row is not a winner.

    `status <> 'deleted'` in the adopt read plus the `== 'active'` loop guard
    stop a rename racing a soft-delete from handing back the tombstone: the org
    really is unnamed then, so the insert error must surface rather than being
    reported as a successful mint.
    """
    cp = _WinnerLandsAfterTheGuards(
        tables={"graphs": [{"id": "g_tomb", "org_id": "org-adopt-tomb",
                            "name": "Team HQ", "kind": "default",
                            "namespace": "org_org-adopt-tomb",
                            "status": "deleted"}]},
        fail={("graphs", "POST"): 'duplicate key value violates unique '
                                 'constraint "uq_graphs_org_name_active"'})
    with pytest.raises(RuntimeError):
        ensure_default_graph_row(cp, "org-adopt-tomb", "org_org-adopt-tomb")


# ── The dream drain's carried tenant (#10025 review F2) ──────────────────────
# The dream pass is the REST lane's SECOND writer: `_enqueue_dream` carries the
# request's tenant across the queue and `_bind_dream_record_tenant` binds it to
# the SDK the drain opens. The call site used to gate the carry on
# `graph_namespace`, i.e. on `org["graph_id"]` — so an ORG-WIDE API KEY (the
# credential shape the issue measured on all 128 production keys) carried
# nothing and every `ConfidenceChanged` that pass emitted was refused
# `no_tenant`, while two docstrings this PR rewrote asserted the opposite.
# The rule that replaces the gate is "the tenant must name the graph the drain
# opens" — and for an org-wide credential that is the default graph, which is
# byte-identical to the graph `_data_sdk` opens for the same request.


def _dream_tenant_or_skip(org, graph_namespace):
    from tortoise import hosted_api

    return hosted_api._dream_record_tenant(org, graph_namespace)


@pytest.fixture
def _control_plane_ids(monkeypatch):
    """Arm the control-plane id source `record_tenant_for` requires.

    `_resolve_record_tenant` asks `is_supabase_enabled()` on the hosted lane; a
    local run has no Supabase configured, and without this every shape would
    answer `None` for the wrong reason — so the assertions below would pass on a
    resolver that was never asked. Imported inside the seam, hence patched on
    the module attribute.
    """
    from tortoise import supabase_control as sc

    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    return True


def test_an_org_wide_key_carries_its_default_tenant_to_the_dream_drain(
        _control_plane_ids) -> None:
    """The credential the issue measured (all 128 were org-wide) names the
    default graph, and its dream records must be filed under it.

    RED before the fix: the call site passed `record_tenant=... if _dream_ns
    else None`, and `_dream_ns` is None for every org-wide key, so this returned
    None and the drain stayed refused `no_tenant`.
    """
    org = {"org_id": "org-wide", "graph_id": None,
           "default_graph_id": DEFAULT_ROW_ID}

    assert _dream_tenant_or_skip(org, None) == ("org-wide", DEFAULT_ROW_ID)


def test_an_unresolved_credential_still_carries_no_tenant(
        _control_plane_ids) -> None:
    """Unbound is the right answer for a shape that names no default graph.

    A dict with no `default_graph_id` is a credential whose org had no
    nameable default row (the resolvers stamp the field for every class since
    #10046), so the refusal is NAMED (`no_tenant`) rather than a guess — and
    the wiring above must not have turned that refusal into a wildcard.
    """
    assert _dream_tenant_or_skip({"org_id": "org-session", "graph_id": None},
                                 None) is None
    # The `"default"` SENTINEL is not an id (the org-list payload carries it),
    # so it is refused too rather than filed under a graph the record's FK
    # cannot name.
    assert _dream_tenant_or_skip({"org_id": "org-sentinel",
                                  "graph_id": None,
                                  "default_graph_id": "default"}, None) is None


def test_a_graph_bound_key_carries_its_own_graph_tenant(
        _control_plane_ids) -> None:
    """The arm that already worked stays working, and it answers with ITS graph
    — never the org's default."""
    org = {"org_id": "org-bound", "graph_id": "g_custom",
           "graph_namespace": "org_org-bound_g_custom",
           "default_graph_id": DEFAULT_ROW_ID}

    assert _dream_tenant_or_skip(org, "org_org-bound_g_custom") == (
        "org-bound", "g_custom")


def test_a_graph_bound_key_with_a_falsy_namespace_carries_no_tenant(
        _control_plane_ids) -> None:
    """The #9188 N3 hazard: this is the ONE shape that must stay refused.

    With a falsy namespace the enqueue falls back to the ORG-WIDE key, whose
    drain opens the DEFAULT graph — while the credential's tenant names the
    custom graph the request meant to write. Carrying it would file the pass's
    rows under a graph it never touched. (`_data_sdk` refuses the org first with
    403 GRAPH_NOT_FOUND, so this is latent rather than live; the guard keeps it
    that way, and is therefore asserted rather than assumed.)
    """
    for falsy in ("", None):
        org = {"org_id": "org-falsy", "graph_id": "g_custom",
               "graph_namespace": falsy, "default_graph_id": DEFAULT_ROW_ID}
        assert _dream_tenant_or_skip(org, None) is None, falsy
