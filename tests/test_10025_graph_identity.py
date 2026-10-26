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
    ensure_default_graph_row,
    graph_metadata,
    provision_org,
    provision_org_with_token,
    set_graph_name,
    set_graph_recording,
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


# ── 5b. A rename targets the row the CALLER NAMED, not a re-resolved one ────
#
# A tombstoned `kind='default'` row and an active one are a LEGAL pair: the
# partial unique index is on `(org_id, name) WHERE status <> 'deleted'`, and
# `20261028000001`'s guard mints a second row precisely when the only default
# row is not active. PostgreSQL returns the two in unspecified order, so a
# status-blind re-read that PATCHes `rows[0]` can write a row the caller never
# named and still return True — the API reports success, the visible rename
# never happens.

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


def test_renaming_the_default_by_its_id_never_patches_a_tombstone() -> None:
    """RED ON THE PRE-FIX CODE: the default fall-through re-read
    `(org_id, kind='default')` with NO status filter and PATCHed
    `rows[0]["id"]`, so a rename that named the ACTIVE row could land on the
    tombstone and return True."""
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
    state — an org nobody can name, with nothing in any log to say so."""
    cp = _FakeCP(tables={"organizations": [
        {"id": "org-1", "graph_name": "team_org-1"}]},
        fail={"graphs": "relation does not exist"})
    with pytest.raises(RuntimeError):
        ensure_default_graph_row(cp, "org-1", "team_org-1")
