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
    is unreadable, so the raise would come from the mint and nowhere else.
    """
    from tests.fake_control_plane import FakeControlPlane

    class _NoGraphsCP(FakeControlPlane):
        """The provision RPC works; the `graphs` table does not."""

        def query(self, table, *a, **kw):  # type: ignore[override]
            if table == "graphs":
                raise RuntimeError("graphs unreadable")
            return super().query(table, *a, **kw)

    for wrapper in (provision_org, provision_org_with_token):
        cp = _NoGraphsCP()
        extra = {"p_signup_token_hash": "sha256:token-hash"} \
            if wrapper is provision_org_with_token else {}
        # Must NOT raise: the org is provisioned, only the default row is not.
        wrapper(cp, **_provision_params("org-f1"), **extra)
        assert default_graph_id_for(cp, "org-f1") is None, (
            "the unreadable table answered a tenant")


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
