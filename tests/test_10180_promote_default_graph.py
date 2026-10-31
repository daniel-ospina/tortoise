"""#10180 — nothing could MOVE the default graph: the promote verb, its
invariant, and the receipt that names where a capture landed.

The defect (measured 2026-10-27 on the issue): a team could create graphs and
mint a key bound to one, but every writer of ``kind='default'`` was a CREATION
path, so the default a team got was the default it kept for the life of the
account. Moving a team onto a graph it outgrew needed a hand-edited
``public.graphs`` row — unversioned, untested, and invisible to the readers that
derive the default from it.

What these tests pin, and what each one FAILS ON:

* the verb moves the ``kind='default'`` marker AND the org's default namespace
  (``organizations.graph_name``), in ONE RPC → the graph the default names and
  the namespace it lands in cannot disagree;
* the one-live-default INVARIANT is the DB's (``uq_graphs_org_default_active``,
  migration ``20261028000001``) — asserted in the PGlite suite
  ``supabase/tests/20261102000001_graphs_promote_default.sql`` and emulated by
  the control-plane double here, so a verb that left TWO defaults cannot pass;
* refusals name the reason (unknown/foreign 404, trash 409 with the restore
  verb, non-active 409, current default idempotent 200);
* an org-wide credential follows the promoted default (``_data_sdk`` /
  ``_data_graph_name``), byte-identically when nothing was promoted;
* the capture receipt NAMES the graph (``resp["graph"]``), additively.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import pytest
from fastapi.testclient import TestClient

import tortoise.hosted_api as ha
from tests._http_fixtures import patched_tortoise_sdk
from tests.fake_control_plane import FakeControlPlane
from tests.test_export_delete import _close_seed_sdks, _enable_supabase

TEAM = "team-10180"
OTHER_TEAM = "team-10180-other"
OWNER = "9f2c1a40-0000-4a00-8000-0000000000a1"
MEMBER = "9f2c1a40-0000-4a00-8000-0000000000a2"
OLD_ID = "g_10180default0001"
NEW_ID = "g_10180promote0002"
OTHER_ID = "g_10180foreign0003"

_ORG = {
    "id": TEAM, "name": "Promote Org", "tier": "team",
    "max_users": None, "max_graphs": None, "graph_size_cap": 500000,
    "ops_allowance": None,
    # The org's DEFAULT namespace (graph 0) — what a pre-promotion org-wide
    # credential opens, and what the promotion must move off.
    "graph_name": f"org_{TEAM}",
}


def _seed(fake: FakeControlPlane) -> None:
    fake.seed("organizations", [dict(_ORG)])
    fake.seed("org_memberships", [
        {"id": "m-own", "org_id": TEAM, "user_id": OWNER, "role": "owner",
         "status": "active"},
        {"id": "m-mem", "org_id": TEAM, "user_id": MEMBER, "role": "member",
         "status": "active"},
    ])
    fake.seed("graphs", [
        {"id": OLD_ID, "org_id": TEAM, "name": "default", "kind": "default",
         "namespace": f"org_{TEAM}", "status": "active", "recording": None},
        {"id": NEW_ID, "org_id": TEAM, "name": "new-extractor",
         "kind": "custom", "namespace": f"org_{TEAM}_{NEW_ID}",
         "status": "active", "recording": None},
    ])


@pytest.fixture
def sb(monkeypatch):
    """Supabase-mode TestClient with a fake control plane + temp DB."""
    fake = FakeControlPlane({"organizations": [], "api_keys": [],
                             "org_memberships": [], "invitations": [],
                             "graphs": []})
    _enable_supabase(monkeypatch, fake)
    _seed(fake)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "promote.db")
        with patched_tortoise_sdk(db_path):
            try:
                with TestClient(ha.app) as tc:
                    yield tc, fake, db_path
            finally:
                _close_seed_sdks()


def _as_session(user_id: str = OWNER, org_id: str = TEAM) -> None:
    ctx = {"org_id": org_id, "session_user_id": user_id, "auth_lane": "session"}
    ha.app.dependency_overrides[ha.get_current_org_session] = lambda: ctx


def _as_key(*, scopes: list[str], org_id: str = TEAM) -> None:
    ctx = {"org_id": org_id, "key_id": "k-10180", "scopes": list(scopes),
           "delegation_depth": None, "legacy_full_access": not scopes}
    ha.app.dependency_overrides[ha.get_current_org_session] = lambda: ctx


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    ha.app.dependency_overrides.pop(ha.get_current_org_session, None)


def _defaults(fake: FakeControlPlane, org_id: str = TEAM) -> list[dict]:
    return fake._live_default_rows(org_id)


# ── 1. The verb ────────────────────────────────────────────────────────────

def test_promote_moves_the_kind_and_the_org_namespace(sb):
    """ONE call: the row that is the default AND the namespace org-wide
    credentials open both move to the target.

    FAIL-ON: a verb that flipped only `kind` leaves the target's `namespace`
    (its own storage) at odds with `organizations.graph_name` — then
    `graph_metadata` publishes the default with the OLD namespace while
    `stable_id` names the NEW row, and the backup sweep dumps the old graph
    under the "default" label. Asserting the org row here is what catches it.
    """
    tc, fake, _ = sb
    _as_key(scopes=[])
    r = tc.post(f"/v1/graphs/{NEW_ID}/promote?org_id={TEAM}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["graph_id"] == NEW_ID
    assert body["already_default"] is False
    assert body["previous_default_graph_id"] == OLD_ID
    assert body["namespace"] == f"org_{TEAM}_{NEW_ID}"
    assert body["record_tenant"] == {"org_id": TEAM, "graph_id": NEW_ID}

    rows = {g["id"]: g for g in fake.tables["graphs"]}
    assert rows[NEW_ID]["kind"] == "default"
    assert rows[OLD_ID]["kind"] == "custom", "the old graph must be KEPT"
    # The old graph keeps its OWN namespace: no data moved.
    assert rows[OLD_ID]["namespace"] == f"org_{TEAM}"
    # The org's default data-plane pointer follows the promoted graph.
    assert fake.tables["organizations"][0]["graph_name"] == rows[NEW_ID]["namespace"]
    assert len(_defaults(fake)) == 1


def test_the_response_states_the_in_flight_consequence(sb):
    """The switch must say what happens to work already admitted — a caller
    cannot infer it from the id, and 'silently re-home them' would be wrong."""
    tc, _, _ = sb
    _as_session()
    r = tc.post(f"/v1/graphs/{NEW_ID}/promote?org_id={TEAM}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["org_wide_credentials_land_in"] == f"org_{TEAM}_{NEW_ID}"
    assert "not re-homed" in body["in_flight_captures"]


def test_promoting_the_current_default_is_idempotent_not_an_error(sb):
    """Retry/refresh safety: no error, and NO write (so a retry cannot demote
    and re-promote the same row)."""
    tc, fake, _ = sb
    _as_session()
    r = tc.post(f"/v1/graphs/{OLD_ID}/promote?org_id={TEAM}")
    assert r.status_code == 200, r.text
    assert r.json()["already_default"] is True
    assert {g["id"]: g["kind"] for g in fake.tables["graphs"]} == {
        OLD_ID: "default", NEW_ID: "custom"}
    assert fake.tables["organizations"][0]["graph_name"] == f"org_{TEAM}"
    # The idempotent arm still goes through the ONE RPC (which is what knows
    # the current default); it is the SQL that performs no write.
    assert [fn for fn, _ in fake.rpc_calls] == ["promote_default_graph"]


def test_the_literal_default_sentinel_resolves_to_a_real_id(sb):
    """The neighbouring endpoints accept the `default` sentinel; so does this
    one — and it resolves it to the row that IS the default (idempotent)."""
    tc, _, _ = sb
    _as_session()
    r = tc.post("/v1/graphs/default/promote?org_id=" + TEAM)
    assert r.status_code == 200, r.text
    assert r.json()["graph_id"] == OLD_ID
    assert r.json()["already_default"] is True


# ── 2. Refusals that name the reason ───────────────────────────────────────

def test_unknown_graph_is_404(sb):
    tc, _, _ = sb
    _as_session()
    r = tc.post(f"/v1/graphs/g_doesnotexist/promote?org_id={TEAM}")
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["error_code"] == "GRAPH_NOT_FOUND"


def test_a_graph_of_another_org_is_404_no_existence_oracle(sb):
    """Cross-org must not even reveal that the id exists: same 404 as unknown."""
    tc, fake, _ = sb
    fake.seed("graphs", [{"id": OTHER_ID, "org_id": OTHER_TEAM,
                          "name": "theirs", "kind": "custom",
                          "namespace": "org_theirs", "status": "active"}])
    _as_session()
    r = tc.post(f"/v1/graphs/{OTHER_ID}/promote?org_id={TEAM}")
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["error_code"] == "GRAPH_NOT_FOUND"
    assert {g["id"]: g["kind"] for g in fake.tables["graphs"]}[OLD_ID] == "default"


def test_a_trashed_graph_is_refused_and_names_the_restore_verb(sb):
    """A tombstone is not a silent 404: the remedy differs, so the refusal
    must say it (the trash surface is `POST /v1/graphs/trash/{id}/restore`)."""
    tc, fake, _ = sb
    for g in fake.tables["graphs"]:
        if g["id"] == NEW_ID:
            g["status"] = "deleted"
    _as_session()
    r = tc.post(f"/v1/graphs/{NEW_ID}/promote?org_id={TEAM}")
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["error_code"] == "GRAPH_IN_TRASH"
    assert "/trash/" in detail["message"] and "restore" in detail["message"]
    # Refused means NOTHING moved.
    assert {g["id"]: g["kind"] for g in fake.tables["graphs"]}[OLD_ID] == "default"
    assert fake.tables["organizations"][0]["graph_name"] == f"org_{TEAM}"


def test_a_non_active_status_is_refused(sb):
    """`graphs.status` is free text with no CHECK — promoting a row no reader
    can see would mint a default that resolves for nobody."""
    tc, fake, _ = sb
    for g in fake.tables["graphs"]:
        if g["id"] == NEW_ID:
            g["status"] = "archived"
    _as_session()
    r = tc.post(f"/v1/graphs/{NEW_ID}/promote?org_id={TEAM}")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error_code"] == "GRAPH_NOT_ACTIVE"


def test_a_key_without_team_manage_is_403(sb):
    """A data-plane key must not move an org-wide pointer."""
    tc, fake, _ = sb
    _as_key(scopes=["graphs:write"])
    r = tc.post(f"/v1/graphs/{NEW_ID}/promote?org_id={TEAM}")
    assert r.status_code == 403, r.text
    assert {g["id"]: g["kind"] for g in fake.tables["graphs"]}[OLD_ID] == "default"


def test_a_non_owner_member_is_403(sb):
    tc, _, _ = sb
    _as_session(user_id=MEMBER)
    r = tc.post(f"/v1/graphs/{NEW_ID}/promote?org_id={TEAM}")
    assert r.status_code == 403, r.text


def test_a_cross_org_credential_is_404(sb):
    tc, _, _ = sb
    _as_session(org_id=OTHER_TEAM)
    r = tc.post(f"/v1/graphs/{NEW_ID}/promote?org_id={TEAM}")
    assert r.status_code == 404, r.text


# ── 3. THE INVARIANT: exactly one live default row per org ─────────────────

def test_promotion_never_leaves_two_defaults(sb):
    """The move is demote-then-promote INSIDE one transaction, so the org
    always has exactly one live default.

    FAIL-ON: a verb that promoted the target and left the old row's kind alone
    (the obvious wrong implementation) puts TWO live defaults in the table —
    every singular reader (`rows[0]`) then picks arbitrarily, one org's record
    rows split across two graph ids, and `stable_id`/the displayed default name
    flicker per request. This test reads the ROWS, not the response.
    """
    tc, fake, _ = sb
    _as_session()
    r = tc.post(f"/v1/graphs/{NEW_ID}/promote?org_id={TEAM}")
    assert r.status_code == 200, r.text
    live = _defaults(fake)
    assert len(live) == 1, f"expected exactly one live default, got {live}"
    assert live[0]["id"] == NEW_ID


def test_the_double_rejects_a_second_live_default():
    """The invariant is the DATABASE's, so the double enforces it too.

    If the fake let two live defaults accumulate, every assertion above could
    pass against a state real Postgres rejects — a false-green generator for
    exactly the defect #10180 closes. This seeds the pair directly and proves
    the check fires (real Postgres: `uq_graphs_org_default_active`, executed in
    `supabase/tests/20261028000001_graphs_default_row_stable_id.sql` §2).
    """
    fake = FakeControlPlane({"organizations": [], "api_keys": [],
                             "org_memberships": [], "invitations": [],
                             "graphs": []})
    _seed(fake)
    fake.tables["graphs"].append(
        {"id": "g_10180dupe00004", "org_id": TEAM, "name": "dupe",
         "kind": "default", "namespace": "org_dupe", "status": "active"})
    with pytest.raises(RuntimeError, match="uq_graphs_org_default_active"):
        fake._assert_one_live_default(TEAM)


def test_the_verb_refuses_to_run_when_the_index_is_already_violated():
    """Pre-existing drift (two live defaults, raw SQL/ops) must not be
    'resolved' by a promotion that silently picks one — the refusal is what
    keeps the repair an explicit operator action."""
    fake = FakeControlPlane({"organizations": [], "api_keys": [],
                             "org_memberships": [], "invitations": [],
                             "graphs": []})
    _seed(fake)
    fake.tables["graphs"].append(
        {"id": "g_10180dupe00005", "org_id": TEAM, "name": "dupe",
         "kind": "default", "namespace": "org_dupe", "status": "active"})
    assert len(fake._live_default_rows(TEAM)) == 2
    with pytest.raises(RuntimeError, match="uq_graphs_org_default_active"):
        fake.rpc("promote_default_graph",
                 {"p_org_id": TEAM, "p_graph_id": NEW_ID},
                 representation=True)
    # …and nothing moved: the drift is still exactly as the operator left it.
    assert {g["id"]: g["kind"] for g in fake.tables["graphs"]}[NEW_ID] == "custom"

# ── 4. ATOMICITY SHAPE: one RPC, one transaction, serialized on the org ────

def test_the_move_is_a_single_rpc_call(sb, monkeypatch):
    """Atomicity cannot be assembled from two PostgREST table writes: demote
    and promote would be two commits with a ZERO-default window between them,
    and promote-first violates the unique index. So the verb must be exactly
    ONE RPC call — and no `graphs` PATCH — per promotion.

    FAIL-ON: an implementation that patches the old row then the new one makes
    `graphs` PATCHes appear here (and a crash between them leaves the org with
    no default at all).
    """
    tc, fake, _ = sb
    _as_session()
    writes: list[str] = []
    original = fake.query

    def _recording_query(table, **kwargs):
        if kwargs.get("method", "GET") != "GET":
            writes.append(f"{kwargs.get('method')} {table}")
        return original(table, **kwargs)

    monkeypatch.setattr(fake, "query", _recording_query)
    before = len(fake.rpc_calls)
    r = tc.post(f"/v1/graphs/{NEW_ID}/promote?org_id={TEAM}")
    assert r.status_code == 200, r.text
    assert fake.rpc_calls[before:] == [
        ("promote_default_graph", {"p_org_id": TEAM, "p_graph_id": NEW_ID})]
    assert not [w for w in writes if w.endswith(" graphs")], (
        "the kind move must not be expressed as table PATCHes")


def test_the_sql_is_one_transaction_serialized_on_the_org_row():
    """The single-statement shape, pinned at the source: the function body
    takes the ORGANIZATION row `FOR UPDATE` before reading the default, so two
    concurrent promotions queue instead of both acting on the same 'current
    default' (which would surface as a unique-violation 500 for the loser).

    A real two-transaction race needs two connections, which PGlite (one
    session) does not have; the SQL suite
    (`supabase/tests/20261102000001_graphs_promote_default.sql`) drives the
    function against real Postgres, and this pin is the complement: it fails if
    the lock is dropped from the file the database will run.
    """
    from pathlib import Path

    path = (Path(__file__).resolve().parent.parent / "supabase" / "migrations"
            / "20261102000001_graphs_promote_default.sql")
    assert path.exists(), f"the migration is missing: {path}"
    sql = "\n".join(line.split("--")[0] for line in
                    path.read_text(encoding="utf-8").splitlines())
    body = sql.split("CREATE OR REPLACE FUNCTION public.promote_default_graph")
    assert len(body) == 2, "the promote RPC is not defined exactly once"
    fn = body[1]
    assert "FOR UPDATE" in fn, "the promotion is not serialized on a row"
    assert fn.index("FOR UPDATE") < fn.index("'custom'"), (
        "the lock must be taken BEFORE the demote, or it guards nothing")
    # Demote precedes promote — the ONLY order the unique index permits.
    assert fn.index("SET kind = 'custom'") < fn.index("SET kind = 'default'")


# ── 5. An org-wide credential follows the promoted default ────────────────

def test_org_wide_credential_opens_the_promoted_default(monkeypatch):
    """The record tenant is not the only thing that moves: org-wide credentials
    must WRITE where the default now is, or the graph and the record disagree.

    FAIL-ON: leave `_data_sdk` on `namespace=org_id` and this returns
    `namespace=''`/the old graph 0 — the capture lands in the demoted graph
    while its rows are filed under the promoted one.
    """
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_role_test")
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
    opened: list[dict] = []

    class _Sdk:
        def _bind_record_tenant(self, *a, **k):
            return None

    def _fake_make_sdk(**kwargs):
        opened.append(kwargs)
        return _Sdk()

    monkeypatch.setattr(ha, "_make_sdk", _fake_make_sdk)
    org = {"org_id": TEAM, "graph_id": None,
           "graph_namespace": f"org_{TEAM}_{NEW_ID}",
           "default_graph_id": NEW_ID}
    assert ha._data_graph_name(org) == f"org_{TEAM}_{NEW_ID}"
    ha._data_sdk(org)
    assert opened == [{"graph_name": f"org_{TEAM}_{NEW_ID}"}]


def test_unpromoted_org_wide_credential_is_byte_identical(monkeypatch):
    """The regression gate: for every org that has NOT promoted anything, the
    hosted lane stores `graph_name == org_{org_id}`, which is exactly what
    `namespace=org_id` derives — so the open must be unchanged."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_role_test")
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
    opened: list[dict] = []

    class _Sdk:
        def _bind_record_tenant(self, *a, **k):
            return None

    monkeypatch.setattr(ha, "_make_sdk",
                        lambda **kw: (opened.append(kw), _Sdk())[1])
    # A pre-#10180 credential shape: no graph_namespace at all (e.g. an OAuth
    # `oat_` token) still opens the historical default graph.
    ha._data_sdk({"org_id": TEAM, "graph_id": None})
    assert opened == [{"namespace": TEAM}]
    assert ha._data_graph_name({"org_id": TEAM, "graph_id": None}) == (
        ha._graph_name_for_namespace(TEAM))
    # …and a hosted credential whose graph_namespace IS the historical name
    # resolves to the same physical graph, so nothing moves for it either.
    assert (ha._data_graph_name({"org_id": TEAM, "graph_id": None,
                                 "graph_namespace": f"org_{TEAM}"})
            == ha._graph_name_for_namespace(TEAM))


def test_registry_lane_is_untouched_by_the_flip(monkeypatch):
    """A recorded decision in `_data_sdk` is that the selfhost registry lane's
    `org_{name}` namespace must never be used to open its default graph. The
    flip is hosted-only, so this asserts it stays that way rather than
    trusting the comment."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
    org = {"org_id": TEAM, "graph_id": None, "graph_namespace": "org_named"}
    assert ha._default_graph_name(org) is None
    assert ha._data_graph_name(org) == ha._graph_name_for_namespace(TEAM)


# ── 6. The receipt names the graph (additive) ─────────────────────────────

def test_the_capture_receipt_names_the_graph_it_landed_in():
    """"Which graph did this land in?" must be SHOWN, not inferred — and it
    stopped being inferable once the default could be promoted.

    ADDITIVE: `resp["graph"]` is a NEW object; the receipt's existing fields are
    untouched (asserted by the keys still being present).
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "tortoise"
           / "hosted_api.py").read_text(encoding="utf-8")
    # The receipt is built in ONE place and must carry the additive key there.
    assert 'resp["graph"] = {' in src
    marker = src.split('resp["graph"] = {', 1)[1].split("}", 1)[0]
    assert '"id": _receipt_tenant[1] if _receipt_tenant else None' in marker
    assert '"namespace": _data_graph_name(org)' in marker
    # No existing receipt field was renamed or removed to make room.
    for field in ('"session_id"', '"turns"', '"extracted"', '"points"',
                  '"surfaced"', '"extraction_mode"', '"errors"',
                  '"warnings"', '"diagnostics"', '"stats"',
                  '"capture_redactions"', '"first_capture"'):
        assert field in src, f"receipt field {field} disappeared"


def test_the_receipt_graph_identity_is_resolvable_without_a_capture():
    """The two halves the receipt reports are the two the request already
    resolved — no extra read, no guess: the record tenant (from the credential)
    and the physical graph name (`_data_graph_name`)."""
    from tortoise.record_writer import record_tenant_for

    org = {"org_id": TEAM, "graph_id": None, "default_graph_id": NEW_ID,
           "graph_namespace": f"org_{TEAM}_{NEW_ID}"}
    assert record_tenant_for(org, control_plane_ids=True) == (TEAM, NEW_ID)
    # A lane that cannot name a tenant reports null rather than inventing one.
    assert record_tenant_for({"org_id": TEAM, "graph_id": None},
                            control_plane_ids=True) is None


# ── 7. The gate: nothing moves for an org that never promoted ─────────────
# The guarantee is DATA-driven, not lane-driven: `moved_default_namespace`
# returns None unless the value is a PER-GRAPH namespace of THIS org
# (`org_{org_id}_{gid}`), which only a promotion can put in
# `organizations.graph_name`. These are the shapes that must NOT flip.

def _hosted(monkeypatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_role_test")
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")


@pytest.mark.parametrize("namespace", [
    f"org_{TEAM}",             # untouched default (the overwhelmingly common case)
    f"team_{TEAM}",            # legacy pre-#3543 shape — flipping would re-home data
    "org_some_other_name",     # registry `sdk.org_create org_{name}` shape (#2023)
    f"org_{TEAM}",             # explicit repeat: the historical value
    None, "",                  # absent namespace → historical open, never a guess
])
def test_the_gate_leaves_every_unmoved_namespace_alone(monkeypatch, namespace):
    """FAIL-ON: drop the gate (return any namespace the credential carries) and
    `team_{TEAM}` / `org_{name}` flip too — silently moving a legacy org's data
    access to a graph it never opened, the #2023 regression this pin exists to
    prevent (and, for `team_`, a graph holding none of its data).

    ⚠️ KNOWN LIMIT, not a hidden one: the gate is a PREFIX test, so a pre-#1903
    row whose stored `graph_name` were `org_{name}` with a name beginning
    `{org_id}_` would be indistinguishable from a promotion. Org ids are 26-char
    hex (a user-chosen name starting with one is not a live shape), and pinning
    the derivation properly is #10361.
    """
    from tortoise.supabase_control import moved_default_namespace

    _hosted(monkeypatch)
    assert moved_default_namespace(TEAM, namespace) is None


def test_the_gate_only_moves_a_per_graph_namespace(monkeypatch):
    from tortoise.supabase_control import moved_default_namespace

    _hosted(monkeypatch)
    assert moved_default_namespace(TEAM, f"org_{TEAM}_{NEW_ID}") == (
        f"org_{TEAM}_{NEW_ID}")
    # Another org's per-graph namespace is not this org's default.
    assert moved_default_namespace(TEAM, f"org_{OTHER_TEAM}_{NEW_ID}") is None
    # Registry mode has no `public.graphs` row to move — no flip, ever.
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
    assert moved_default_namespace(TEAM, f"org_{TEAM}_{NEW_ID}") is None


# ── 8. The lanes that build their OWN SDK follow the same pointer ──────────
# Converting only `_data_sdk` was the inherited lane's defect: the dream drain,
# the event sweep, the pack catalog, the quota counter and the seed/indexing
# runners would keep reading and writing the DEMOTED graph while filing record
# rows under the promoted one.

def _capture_make_sdk(monkeypatch):
    opened: list[dict] = []

    class _Sdk:
        def _get_proj(self):  # pragma: no cover - shape only
            return None

        def _bind_record_tenant(self, *a, **k):
            return None

    monkeypatch.setattr(
        ha, "_make_sdk", lambda **kw: (opened.append(kw), _Sdk())[1])
    return opened


def _fake_org_row(monkeypatch, graph_name: str):
    """A control plane that answers the one PK read `_org_sdk` makes."""
    fake = FakeControlPlane({"organizations": [], "api_keys": [],
                             "org_memberships": [], "invitations": [],
                             "graphs": []})
    _enable_supabase(monkeypatch, fake)
    _seed(fake)
    fake.tables["organizations"][0]["graph_name"] = graph_name
    return fake


def test_org_sdk_is_byte_identical_for_an_unpromoted_org(monkeypatch):
    """The regression gate for the 15 converted call sites: for an org that has
    not promoted, `_org_sdk` must build EXACTLY the SDK `_make_sdk(namespace=
    org_id)` built — not merely "the same graph": `graph_name=` would leave
    `sdk._namespace` None and change the fallback-snapshot identity key."""
    _hosted(monkeypatch)
    _fake_org_row(monkeypatch, f"org_{TEAM}")
    opened = _capture_make_sdk(monkeypatch)
    ha._org_sdk(TEAM)
    assert opened == [{"namespace": TEAM}]


def test_org_sdk_follows_a_promoted_default(monkeypatch):
    """FAIL-ON: leave the lanes on `namespace=org_id` and this opens the graph
    the promotion DEMOTED — the event sweep never expires the new default's
    events, the dream pass writes into the old graph while the record names the
    new one, and the points cap counts the wrong graph (money gate)."""
    _hosted(monkeypatch)
    _fake_org_row(monkeypatch, f"org_{TEAM}_{NEW_ID}")
    opened = _capture_make_sdk(monkeypatch)
    ha._org_sdk(TEAM)
    assert opened == [{"graph_name": f"org_{TEAM}_{NEW_ID}"}]


def test_org_sdk_takes_the_namespace_it_is_given_without_a_read(monkeypatch):
    """`namespace=` is the no-I/O shortcut for a caller that already holds it
    (the authenticated org dict carries `graph_name`) — and it goes through the
    SAME gate, so it cannot bypass it."""
    _hosted(monkeypatch)
    opened = _capture_make_sdk(monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(ha, "_org_default_namespace",
                        lambda oid: calls.append(oid) or "SHOULD-NOT-BE-USED")
    ha._org_sdk(TEAM, namespace=f"org_{TEAM}")
    ha._org_sdk(TEAM, namespace=f"org_{TEAM}_{NEW_ID}")
    assert opened == [{"namespace": TEAM},
                      {"graph_name": f"org_{TEAM}_{NEW_ID}"}]
    assert calls == [], "the given namespace must not cost a control-plane read"


def test_org_sdk_falls_back_to_the_historical_open_when_the_read_fails(monkeypatch):
    """A control-plane blip must not turn every converted lane into an outage,
    and must never make one WORSE than the code it replaces: the historical
    open is what those lanes opened before this change."""
    _hosted(monkeypatch)

    class _Broken:
        def query(self, *a, **k):
            raise RuntimeError("postgrest down")

    monkeypatch.setattr(ha, "get_control_plane", lambda: _Broken(), raising=False)
    monkeypatch.setattr("tortoise.supabase_control.get_control_plane",
                        lambda: _Broken())
    opened = _capture_make_sdk(monkeypatch)
    ha._org_sdk(TEAM)
    assert opened == [{"namespace": TEAM}]


def _credential(**over) -> dict:
    """A RESOLVED credential dict, in the shape the auth seams actually build.

    ⚠️ Deliberately no `graph_name` key: an earlier version of this test used
    one, so the gate's no-read shortcut never fired and the assertion passed on
    a shape production cannot produce (`resolve_api_key`, `_session_user_org`
    and the OAuth resolver all stamp `graph_namespace`).
    """
    org = {"org_id": TEAM, "graph_id": None,
           "graph_namespace": f"org_{TEAM}_{NEW_ID}", "scopes": [],
           "legacy_full_access": True, "delegation_depth": None}
    org.update(over)
    return org


def test_the_quota_gate_counts_the_promoted_graph(monkeypatch):
    """The money gate. `_check_org_limit` must hand the counter the DEFAULT
    graph's SDK: counting the demoted graph admits an org past its real cap
    (fail-open, revenue) or refuses writes it is under (customer)."""
    import tortoise.quota as quota

    _hosted(monkeypatch)
    seen: dict = {}
    monkeypatch.setattr(
        quota, "enforce_org_limit",
        lambda limits, resource, sdk=None, **kw: seen.update(
            {"resource": resource, "sdk": sdk}))
    # A read here would also break the gate's fail-CLOSED contract: a read
    # failure would fall back to the demoted graph, i.e. count the wrong graph
    # silently. Prove there is no read at all.
    monkeypatch.setattr(
        ha, "_org_default_namespace",
        lambda oid: pytest.fail("the quota gate must not read the control plane"))

    opened = _capture_make_sdk(monkeypatch)
    ha._check_org_limit(_credential(), "points")
    assert opened == [{"graph_name": f"org_{TEAM}_{NEW_ID}"}], (
        "the points cap must count the graph the writes land in")
    assert seen["sdk"] is not None

    # A control-plane-counted resource must not pay for an SDK it never touches.
    opened.clear()
    ha._check_org_limit(_credential(), "api_keys")
    assert opened == [] and seen["sdk"] is None


def test_a_graph_bound_credential_keeps_the_historical_quota_count(monkeypatch):
    """A graph-bound credential's `graph_namespace` IS a custom graph's — the
    default-graph question does not apply to it, and `_default_graph_name`
    answering it would count a graph the credential never bound."""
    import tortoise.quota as quota

    _hosted(monkeypatch)
    monkeypatch.setattr(
        quota, "enforce_org_limit", lambda *a, **k: None)
    opened = _capture_make_sdk(monkeypatch)
    ha._check_org_limit(
        _credential(graph_id=NEW_ID,
                    graph_namespace=f"org_{TEAM}_{NEW_ID}"), "points")
    assert opened == [{"namespace": TEAM}]
    assert ha._default_graph_name(
        _credential(graph_id=NEW_ID,
                    graph_namespace=f"org_{TEAM}_{NEW_ID}")) is None


def test_the_mcp_lane_follows_a_promoted_default(monkeypatch):
    """MCP is the surface agents actually write through: an org-wide MCP
    credential must open the promoted graph too, or every agent capture lands in
    the demoted one while the record names the new one."""
    import tortoise.mcp_auth as ma

    _hosted(monkeypatch)
    seen: dict = {}

    class _Sdk:
        def __init__(self, **kw):
            seen.update(kw)

    monkeypatch.setattr(ma, "TortoiseSDK", _Sdk)
    tokens = [ma._transport_mode.set("http"),
              ma._current_org_id.set(TEAM),
              ma._current_graph_id.set(None),
              ma._current_graph_namespace.set(f"org_{TEAM}_{NEW_ID}")]
    try:
        ma._get_org_sdk()
    finally:
        for var, tok in zip((ma._transport_mode, ma._current_org_id,
                             ma._current_graph_id,
                             ma._current_graph_namespace), tokens,
                        strict=True):
            var.reset(tok)
    assert seen.get("graph_name") == f"org_{TEAM}_{NEW_ID}"


def test_the_mcp_lane_is_unchanged_for_an_unpromoted_org(monkeypatch):
    import tortoise.mcp_auth as ma

    _hosted(monkeypatch)
    seen: dict = {}

    class _Sdk:
        def __init__(self, **kw):
            seen.update(kw)

    monkeypatch.setattr(ma, "TortoiseSDK", _Sdk)
    tokens = [ma._transport_mode.set("http"),
              ma._current_org_id.set(TEAM),
              ma._current_graph_id.set(None),
              ma._current_graph_namespace.set(f"org_{TEAM}")]
    try:
        ma._get_org_sdk()
    finally:
        for var, tok in zip((ma._transport_mode, ma._current_org_id,
                             ma._current_graph_id,
                             ma._current_graph_namespace), tokens,
                        strict=True):
            var.reset(tok)
    assert seen.get("namespace") == TEAM
    assert "graph_name" not in seen


# ── 9. THE INVENTORY: no org-wide opener is left behind ───────────────────
# The inherited lane converted ONE call site and left the rest, which is the
# defect this test pins: every `_make_sdk(namespace=<org>)` in the file is
# either converted to `_org_sdk` or is on this list WITH the reason it must stay
# historical. A new one fails here, so the class cannot silently re-open.

_ORG_WIDE_OPENER_ALLOWLIST = {
    # → the resolvers' own historical arm (the definition of "not moved");
    #   `_org_sdk_for_credential` is the credential-shaped twin the quota gate uses.
    "_org_sdk": 1,
    "_org_sdk_for_credential": 1,
    # → the credential path's historical arm; the moved arm calls
    #   `_make_sdk(graph_name=...)` above it.
    "_data_sdk": 1,
    # → the canonical-name branch, reached only when `_org_default_namespace`
    #   returned None (a moved default returns earlier, by name).
    "_open_org_graph_sdk": 1,
    # → the SDK is only a `.db` handle; the DELETED graph is `graph_name or
    #   org_{org_id}`, and the purge sweep passes the stored `graph_name`.
    #   ⚠️ That means a hard delete drops the org's DEFAULT graph's storage and
    #   nothing else — every other graph's namespace is left behind. That orphan
    #   class is PRE-EXISTING (a custom graph was never dropped on org delete)
    #   and is filed as #10450; a promotion sharpens it, because after one the
    #   DEMOTED graph holds the team's principal pre-switchover data. Followed up
    #   there rather than fixed here: "follow the default" is the wrong operand
    #   for a purge (the SET of the org's graphs is), and this lane must not
    #   rewrite the #926 retry anchor.
    "_drop_org_graph_impl": 1,
    # → provisioning/creation: each of these MINTS the default row (or runs
    #   inside the same provisioning flow), so no promotion can predate it.
    "provision_tenant": 1,
    "register_user": 3,
    "_eager_provision_org_graph": 2,
}


_OTHER_FILE_ALLOWLIST = {
    # `quota.py` is a LEAF consumer (it must not import hosted_api), and this
    # fallback is only reached when a caller passes no `sdk=`. Every REST caller
    # now passes the default-graph SDK (`hosted_api._check_org_limit`), so this
    # arm survives for direct callers only — and it is the same historical open
    # it always was. Converting it would duplicate the gate in a module that
    # cannot import it.
    "tortoise/quota.py": {"_count_resource": 1},
    # Provisioning: the default row is MINTED by the flow these run in, so no
    # promotion can predate them.
    "tortoise/supabase_control.py": {"provision_org": 1,
                                     "_ensure_onboarding_node_after_provision": 2},
}


def test_every_org_wide_opener_is_converted_or_accounted_for():
    """Enumerate the class with the AST, not by grep — and fail on an
    unaccounted one.

    FAIL-ON: add `_make_sdk(namespace=org_id)` anywhere new (or revert one of
    the converted sites) and this goes red, naming the function. That is the
    check the inherited lane lacked: it converted `_data_sdk` alone, which left
    five lanes writing the demoted graph while filing records under the promoted
    one.
    """
    import ast
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "tortoise"
           / "hosted_api.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    parents: dict = {}
    for _node in ast.walk(tree):
        for _child in ast.iter_child_nodes(_node):
            parents[_child] = _node

    def _enclosing_name(node) -> str:
        cur = node
        while cur in parents:
            cur = parents[cur]
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return cur.name
        return "<module>"

    found: dict[str, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        callee = (fn.id if isinstance(fn, ast.Name)
                  else fn.attr if isinstance(fn, ast.Attribute) else None)
        if callee != "_make_sdk":
            continue
        kw = next((k for k in node.keywords if k.arg == "namespace"), None)
        if kw is None:
            continue
        # Only `namespace=<org id>` counts; `namespace="registry"` / `None` are
        # a different class and are exempt by construction.
        value = kw.value
        if isinstance(value, ast.Constant) and value.value in ("registry", None):
            continue
        name = _enclosing_name(node)
        found[name] = found.get(name, 0) + 1

    assert found == _ORG_WIDE_OPENER_ALLOWLIST, (
        "an org-wide `_make_sdk(namespace=<org>)` opener changed — convert it to "
        f"`_org_sdk` or record why it must stay historical. Found: {found}")

    # The class spans `tortoise/`, not just this file (quota's counter and the
    # provisioning post-steps) — pinned too, so a NEW one there is caught.
    repo_root = Path(__file__).resolve().parent.parent
    for rel, expected in _OTHER_FILE_ALLOWLIST.items():
        other = ast.parse((repo_root / rel).read_text(encoding="utf-8"))
        o_parents: dict = {}
        for _n in ast.walk(other):
            for _c in ast.iter_child_nodes(_n):
                o_parents[_c] = _n

        def _enc(node, _p=o_parents) -> str:
            cur = node
            while cur in _p:
                cur = _p[cur]
                if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return cur.name
            return "<module>"

        wide: dict[str, int] = {}
        for node in ast.walk(other):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            callee = (fn.id if isinstance(fn, ast.Name)
                      else fn.attr if isinstance(fn, ast.Attribute) else None)
            kw = next((k for k in node.keywords if k.arg == "namespace"), None)
            if callee != "_make_sdk" or kw is None:
                continue
            if isinstance(kw.value, ast.Constant) and kw.value.value in (
                    "registry", None):
                continue
            wide[_enc(node)] = wide.get(_enc(node), 0) + 1
        assert wide == expected, (
            f"the org-wide opener class changed in {rel} — see "
            f"_OTHER_FILE_ALLOWLIST. Found: {wide}")


# ── 10. The two holes a review found in the first cut of this verb ────────
# (a) a promote UPDATE that matched ZERO rows — the target trashed between the
#     read and the write — used to return `ok` over an org with no default;
# (b) the idempotent arm used to answer `ok` to an org whose `graph_name` had
#     drifted from the default row's namespace.

def test_the_idempotent_arm_repairs_a_drifted_org_namespace(sb):
    """(b): promoting the current default is the one call whose contract is
    "row and org pointer agree" — so it must converge a drift, not bless it.

    FAIL-ON: an idempotent arm that returns early without touching
    `graph_name` leaves the org's data plane on the graph the promoted row does
    not name (§3 of 20261028000001 violated) while the API answers 200.
    """
    tc, fake, _ = sb
    fake.tables["organizations"][0]["graph_name"] = "org_10180_drifted"
    _as_session()
    r = tc.post(f"/v1/graphs/{OLD_ID}/promote?org_id={TEAM}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["already_default"] is True
    assert body["namespace_repaired"] is True
    assert fake.tables["organizations"][0]["graph_name"] == f"org_{TEAM}"
    # …and no graph row moved: the repair is not a re-promotion.
    assert {g["id"]: g["kind"] for g in fake.tables["graphs"]} == {
        OLD_ID: "default", NEW_ID: "custom"}


def test_a_failed_post_condition_is_a_retryable_conflict_not_a_500(monkeypatch):
    """(a): the SQL function's own post-condition ("exactly one active default,
    and it is the target") is a 409 `PROMOTE_CONFLICT` — nothing was written, so
    the caller retries. Any OTHER unmodelled SQL failure stays a 500: a genuine
    outage must never be dressed up as a caller mistake."""
    from tortoise.supabase_control import (
        PromoteDefaultGraphError,
    )
    from tortoise.supabase_control import (
        promote_default_graph as sb_promote,
    )

    class _Cp:
        def __init__(self, message):
            self.message = message

        def rpc(self, fn, body, representation=True):
            raise RuntimeError(self.message)

    with pytest.raises(PromoteDefaultGraphError) as exc:
        sb_promote(
            _Cp("promote_default_graph: the default did not move (org t has "
                "no single active default at g_x after the move)"),
            TEAM, NEW_ID)
    assert exc.value.status == 409 and exc.value.code == "PROMOTE_CONFLICT"

    with pytest.raises(RuntimeError) as raw:
        sb_promote(_Cp("connection refused"), TEAM, NEW_ID)
    assert not isinstance(raw.value, PromoteDefaultGraphError)


def test_the_sql_locks_the_target_row_and_checks_the_post_condition():
    """(a), pinned where it is enforced. A real two-connection race is not
    expressible in PGlite (one session) or in the double (one thread), so the
    SHAPE is pinned: the target row is locked, and the function refuses to
    return unless exactly one active default exists at the target.

    FAIL-ON: drop the `FOR UPDATE` from the target SELECT and a concurrent trash
    can empty the org's default pointer through the window between the read and
    the promote UPDATE; drop the post-condition and a zero-default state
    returns `ok` (the index cannot see it — no row, no violation)."""
    from pathlib import Path

    path = (Path(__file__).resolve().parent.parent / "supabase" / "migrations"
            / "20261102000001_graphs_promote_default.sql")
    sql = "\n".join(line.split("--")[0] for line in
                    path.read_text(encoding="utf-8").splitlines())
    fn = sql.split("CREATE OR REPLACE FUNCTION public.promote_default_graph")[1]
    # The org row and the target graph row are both locked, org first.
    assert fn.count("FOR UPDATE") == 2, (
        "both the organization row and the TARGET graph row must be locked")
    assert fn.index("FOR UPDATE") < fn.index("FROM public.graphs g"), (
        "the organization lock must be taken BEFORE the graph row is read")
    # The post-condition: exactly one ACTIVE default, and it is the target.
    assert "the default did not move" in fn
    assert "count(*)" in fn and "kind = 'default'" in fn
    post = fn.index("the default did not move")
    assert post > fn.index("SET kind = 'default'"), (
        "the post-condition must be checked AFTER the promote, not before")
    # And it RAISEs, i.e. the transaction (including the demote) rolls back.
    assert "RAISE EXCEPTION" in fn[post - 400:post]


# ── 11. The state the move makes reachable: zero live defaults ────────────
# Before this verb nothing ever changed `kind`, so a DELETE could not tombstone
# a default row. After it, "the graph was custom a moment ago" is a statement
# about the READ, not about the row — so the tombstone must carry the predicate
# itself, not rely on the pre-read.

def test_soft_delete_cannot_tombstone_a_graph_that_became_the_default(
        monkeypatch):
    """A DELETE that read `kind='custom'` before the promotion must not tombstone
    the row afterwards.

    FAIL-ON: `soft_delete_graph`'s PATCH used to filter on id/org only, so this
    interleaving left the org with ZERO live defaults — the record then refuses
    every write `no_tenant` forever, the trashed row is not listed by
    `trash_graphs` (kind='custom' only), and `restore_graph` refuses
    kind='default'. Unreachable state, from a race the verb made possible."""
    from tortoise.supabase_control import soft_delete_graph

    fake = FakeControlPlane({"organizations": [], "api_keys": [],
                             "org_memberships": [], "invitations": [],
                             "graphs": []})
    _seed(fake)
    fake.rpc("promote_default_graph",
             {"p_org_id": TEAM, "p_graph_id": NEW_ID}, representation=True)
    assert len(_defaults(fake)) == 1

    real = fake.query
    calls = {"n": 0}

    def _stale_pre_read(table, **kwargs):
        # The DELETE endpoint reads `kind` first; that read happened BEFORE the
        # promotion and so still says `custom`.
        if calls["n"] == 0:
            calls["n"] = 1
            return [{"kind": "custom", "status": "active"}]
        return real(table, **kwargs)

    monkeypatch.setattr(fake, "query", _stale_pre_read)
    assert soft_delete_graph(fake, TEAM, NEW_ID) is False, (
        "the tombstone must be refused once the row is the default")
    monkeypatch.setattr(fake, "query", real)
    assert [g["status"] for g in fake.tables["graphs"]
            if g["id"] == NEW_ID] == ["active"]
    assert len(_defaults(fake)) == 1


# ── 12. Every hosted auth seam carries the namespace the resolver reads ────
# The resolver is only as good as the FIELD the credential carries. A seam that
# omits `graph_namespace` silently keeps the historical open — the demoted graph
# after a promotion — while its record tenant (resolved from the row) names the
# promoted one. That is how the `oat_` class was missed on the first pass.

def test_the_oauth_lane_carries_the_default_namespace(monkeypatch):
    """`resolve_oauth_access_token` must stamp `graph_namespace`.

    FAIL-ON: drop the stamp (or `graph_name` from `oauth._org_row`) and an
    `oat_` MCP credential resolves to `namespace=org_{org_id}` — the graph a
    promotion DEMOTED — while `record_tenant_for` files its rows under the
    promoted id (`default_graph_id` is stamped right beside it).
    """
    from tortoise import oauth

    _hosted(monkeypatch)
    promoted = f"org_{TEAM}_{NEW_ID}"
    fake = FakeControlPlane({"organizations": [], "api_keys": [],
                             "org_memberships": [], "invitations": [],
                             "graphs": []})
    _seed(fake)
    fake.rpc("promote_default_graph",
             {"p_org_id": TEAM, "p_graph_id": NEW_ID}, representation=True)
    assert fake.tables["organizations"][0]["graph_name"] == promoted
    token = "oat_" + "c" * 40
    fake.seed("oauth_access_tokens", [{
        "token_hash": oauth._sha256(token), "client_id": "client-10180",
        "user_id": OWNER, "org_id": TEAM, "scope": "mcp",
        "expires_at": "2099-01-01T00:00:00+00:00", "revoked_at": None,
    }])
    # No live network: the resolver otherwise reaches GoTrue for the owner
    # email (the #4387 egress guard blocks it). Same wiring the #10046 suite
    # uses for this seam.
    from tortoise import hosted_api as _ha
    from tortoise import supabase_control as _sc
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setattr(_sc, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(_sc, "get_control_plane", lambda: fake)

    async def _noop(*a, **kw):
        return None

    monkeypatch.setattr(_ha, "_abuse_post_auth", _noop)
    monkeypatch.setattr(_ha, "_org_node_sync_limits", lambda oid: {})
    org = oauth.resolve_oauth_access_token(fake, token)
    assert org is not None, "the credential did not resolve"
    assert org["graph_namespace"] == promoted, (
        "the OAuth credential must carry the org's DEFAULT namespace")
    assert org["default_graph_id"] == NEW_ID
    # …and the seam the MCP lane reads answers with it.
    assert ha._default_graph_name(org) == promoted


def test_every_hosted_auth_seam_stamps_the_same_field():
    """Source pin for the CLASS: each hosted seam that resolves an org-wide
    credential must put `graph_namespace` on the dict, because the resolver
    reads that field and nothing else.

    FAIL-ON: a new seam (or a reverted stamp) that resolves an org-wide
    credential without it silently keeps the historical open.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "tortoise"
    seams = {
        "supabase_control.py": '"graph_namespace": graph_namespace',
        "hosted_api.py": '"graph_namespace": row.get("graph_name")',
        "oauth.py": 'org["graph_namespace"] = _default_ns',
    }
    for rel, needle in seams.items():
        src = (root / rel).read_text(encoding="utf-8")
        assert needle in src, (
            f"{rel} no longer stamps the org-wide `graph_namespace`: the "
            "default-graph resolver reads that field and would silently open "
            "the demoted graph after a promotion")


def test_the_mcp_capture_bridge_carries_the_default_namespace():
    """The MCP capture tool hands `_capture_session_impl` a dict it BUILDS, not
    the resolved credential — and an org-wide MCP credential has no `graph_id`,
    so gating the namespace on `graph_id` (as that bridge used to) dropped it on
    exactly the class a promotion moves. The capture then wrote the DEMOTED
    graph while the ambient `_record_tenant_scope` filed its record rows under
    the promoted one.

    FAIL-ON: move the stamp back inside `if _gid:` (indent it one level) and this
    goes red — the MCP lane, which is the surface agents write through."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "tortoise"
           / "mcp_server.py").read_text(encoding="utf-8")
    bridge = src.split("_gid = _current_graph_id.get()", 1)[1].split(
        'if limits.get("max_points")', 1)[0]
    stamps = [ln for ln in bridge.splitlines()
              if 'org["graph_namespace"]' in ln]
    assert stamps, "the MCP capture bridge no longer carries graph_namespace"
    assert stamps[0].startswith('    org["graph_namespace"]'), (
        "the stamp must be UNCONDITIONAL (4-space body indent), not nested "
        f"under `if _gid:`: {stamps[0]!r}")
    # …and the graph-bound half still carries the scope fields.
    assert 'org["graph_id"] = _gid' in bridge


# ── 13. The org-id-only lanes reuse the credential — no read, one answer ────
# Round 2 of the review found the request lanes still reaching the READ form:
# `_maybe_apply_completion` (every capture) and the onboarding projection/PATCH
# both hold only an `org_id`, so each paid a control-plane read on the event loop
# — and on a read failure wrote into the DEMOTED graph. The auth seam now
# publishes the resolved value for the request and they consume it.

def test_the_org_id_only_lanes_reuse_the_requests_own_answer(monkeypatch):
    """`_org_sdk(org_id)` must not read when THIS request already resolved the
    credential, and must open the graph that credential named.

    FAIL-ON: drop the hint (or read before consulting it) and every capture's
    completion gate and every onboarding PATCH pays a synchronous PostgREST read
    on the loop, whose failure path writes onboarding status into the DEMOTED
    graph while the capture's points went to the promoted one."""
    _hosted(monkeypatch)

    class _Unreadable:
        """A control plane that RAISES: any read shows up as the fallback."""

        def query(self, *a, **k):
            raise RuntimeError("control plane unreadable (test)")

    monkeypatch.setattr("tortoise.supabase_control.get_control_plane",
                        lambda: _Unreadable())
    opened = _capture_make_sdk(monkeypatch)

    # The auth seam publishes; the lane consumes — no read, promoted graph.
    ha._publish_default_namespace(_credential())
    ha._org_sdk(TEAM)                       # e.g. _org_proj / _graph_available
    assert opened == [{"graph_name": f"org_{TEAM}_{NEW_ID}"}]

    # The hint is ORG-scoped: a different org in the same context falls back to
    # the read (here unreadable → the historical open), never to this org's graph.
    opened.clear()
    ha._org_sdk(OTHER_TEAM)
    assert opened == [{"namespace": OTHER_TEAM}]


def test_the_event_sweep_takes_the_namespace_from_its_enumeration():
    """The retention sweep must not pay a read per org: `graph_name` rides the
    page it already walks, and the parse must KEEP the column.

    FAIL-ON: the enumeration fetched `graph_name` and dropped it when building
    the dict, so `_org_sdk(..., namespace=None)` took the READ branch for every
    org every hour — and on a read failure purged the DEMOTED graph's events."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "tortoise"
           / "hosted_api.py").read_text(encoding="utf-8")
    assert '"organizations", select=["id", "name", "graph_name"]' in src
    assert 'parsed.append({"org_id": rid, "name": r.get("name"),' in src, (
        "the enumeration must carry graph_name into the dict the sweep reads")
    # …and the sweep passes it, so no read is needed.
    assert '_org_sdk(org["org_id"], namespace=org.get("graph_name"))' in src
