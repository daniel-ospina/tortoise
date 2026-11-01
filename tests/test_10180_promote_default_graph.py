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
    """Supabase-mode TestClient with a fake control plane + temp DB.

    #10180: quiesced via the documented seam (``tests/_http_fixtures``), because
    the promote path now touches the GRAPH as well as the control plane (the
    onboarding carry), which put its latency across the window in which the
    lifespan's un-quiesced boot sweep lands its own
    ``metering_repair_period_bounds`` RPC — the exact `rpc_calls` race that
    helper exists to remove. Without it the three exact-`rpc_calls` assertions
    below read that unrelated sweep as a second call of the verb.
    """
    from tests._http_fixtures import quiesce_testclient_background_work

    quiesce_testclient_background_work(monkeypatch)
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


def test_the_verb_converges_pre_existing_duplicate_default_drift():
    """Pre-existing drift (two live defaults, raw SQL/ops) is REPAIRED by a
    promotion — the verb does not refuse it.

    Read the SQL, not the intent: the migration's demote is

        UPDATE public.graphs g SET kind = 'custom'
         WHERE g.org_id = p_org_id AND g.kind = 'default'
           AND g.status <> 'deleted' AND g.id <> p_graph_id

    with NO row-count guard, and demoting cannot violate a uniqueness index —
    so both drifted rows are demoted, one default remains, and the
    post-condition passes. The migration header lists this very state as one the
    post-condition covers, not among the refusals, and the function returns
    `ok`. This test used to assert the opposite (a raised
    `uq_graphs_org_default_active`) while the double raised on its own, and the
    fake's docstring claims it mirrors the SQL — so the mismatch would have told
    a future lane that a drift guard exists in the verb, or that the verb cannot
    converge a drifted org. It can.

    FAIL-ON: re-add a row-count refusal to the double (the pre-fix shape) and
    this reds — the promotion succeeds against production and raises here.
    """
    fake = FakeControlPlane({"organizations": [], "api_keys": [],
                             "org_memberships": [], "invitations": [],
                             "graphs": []})
    _seed(fake)
    fake.tables["graphs"].append(
        {"id": "g_10180dupe00005", "org_id": TEAM, "name": "dupe",
         "kind": "default", "namespace": "org_dupe", "status": "active"})
    assert len(fake._live_default_rows(TEAM)) == 2
    receipt = fake.rpc("promote_default_graph",
                       {"p_org_id": TEAM, "p_graph_id": NEW_ID},
                       representation=True)
    # The move happened, and the drift is GONE: exactly one live default (the
    # target) — the same after-state the DB's post-condition certifies.
    kinds = {g["id"]: g["kind"] for g in fake.tables["graphs"]}
    assert kinds[NEW_ID] == "default"
    assert kinds[OLD_ID] == "custom"
    assert kinds["g_10180dupe00005"] == "custom", (
        "the demote statement has no `id <> p_graph_id`-only narrowing — every "
        "drifted default row must be demoted")
    live = _defaults(fake)
    assert [g["id"] for g in live] == [NEW_ID], live
    # The receipt reports the FIRST demoted row (plpgsql's non-STRICT INTO).
    assert receipt["ok"] is True
    assert receipt["already_default"] is False
    assert receipt["previous_default_graph_id"] in (OLD_ID, "g_10180dupe00005")

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
    # → the resolvers' own historical arm (the definition of "not moved").
    #   `_org_sdk` now delegates to `_org_sdk_from_name` (split so a background
    #   job can resolve the name in a WORKER and still construct on the loop,
    #   tortoise#7500), so that split's construction is the historical arm too;
    #   `_org_sdk_for_credential` is the credential-shaped twin the quota gate uses.
    "_org_sdk_from_name": 1,
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


def test_the_mcp_capture_lane_resolves_the_default_namespace_itself(monkeypatch):
    """The MCP `tortoise_session_capture` tool builds its own credential dict and
    (because that file is a FROZEN served surface — `tools/surface-guard.py`)
    cannot be changed to carry the default namespace. So the shared impl hydrates
    it: an org-wide caller whose dict lacks the field resolves it there.

    BEHAVIOURAL, not a source grep: the assertions below run the resolver the
    capture impl calls and check the dict it hands the data plane.

    FAIL-ON: without the hydration, an org-wide MCP capture opens
    `org_{org_id}` — the graph a promotion DEMOTED — while its record rows
    (tenant = the credential's `default_graph_id`) name the promoted one: the
    record/data split #10025 exists to remove, on the surface agents write
    through. The guard half below makes sure the fix is NOT put back into the
    frozen file."""
    import asyncio

    _fake_org_row(monkeypatch, f"org_{TEAM}_{NEW_ID}")

    # 1. An org-wide dict with NO namespace (what the MCP tool builds) resolves
    #    the PROMOTED graph — the one the record tenant names.
    got = asyncio.run(ha._hydrate_default_namespace({"org_id": TEAM}))
    assert got["graph_namespace"] == f"org_{TEAM}_{NEW_ID}", (
        "an org-wide capture with no namespace in its dict must resolve the "
        "DEFAULT graph — otherwise it writes the graph a promotion demoted")
    assert got["org_id"] == TEAM  # the rest of the dict is untouched

    # 2. A dict that ALREADY carries one is returned untouched — the REST lanes
    #    are not re-resolved (and pay no read).
    carried = {"org_id": TEAM, "graph_namespace": f"org_{TEAM}"}
    assert asyncio.run(ha._hydrate_default_namespace(dict(carried))) == carried

    # 3. A GRAPH-BOUND dict is untouched: its namespace is a CUSTOM graph's, and
    #    "the default graph" is not the question it asks.
    bound = {"org_id": TEAM, "graph_id": NEW_ID,
             "graph_namespace": f"org_{TEAM}_{NEW_ID}"}
    assert asyncio.run(ha._hydrate_default_namespace(dict(bound))) == bound

    # 4. The fix must NOT live in the frozen file: an unconditional stamp there
    #    is a surface change needing owner approval (#3863/#4282).
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "tortoise"
           / "mcp_server.py").read_text(encoding="utf-8")
    bridge = src.split("_gid = _current_graph_id.get()", 1)[1].split(
        'if limits.get("max_points")', 1)[0]
    stamps = [ln for ln in bridge.splitlines()
              if 'org["graph_namespace"]' in ln]
    assert stamps, (
        "the MCP capture bridge stopped stamping `graph_namespace` at all — "
        "that is the graph-BOUND half the frozen tool served before #10180")
    assert all(ln.startswith("        ") for ln in stamps), (
        "tortoise/mcp_server.py serves frozen tools — a top-level (4-space, "
        "UNCONDITIONAL) `org[\"graph_namespace\"]` stamp there is a surface "
        "change needing owner approval (#3863/#4282, tools/surface-guard.py). "
        "It must stay nested under `if _gid:`; put the org-wide fix in "
        "`_hydrate_default_namespace` instead.")


def test_the_mcp_capture_data_half_and_its_record_tenant_are_one_snapshot(monkeypatch):
    """A capture admitted BEFORE a promotion writes the graph it resolved AND
    files its rows under that same graph.

    On this lane the two halves come from two carriers: the DATA half from
    `_hydrate_default_namespace`, the RECORD half from the ambient tenant scope
    `mcp_auth.OrgResolutionMiddleware` installs out of the credential it resolved
    (`record_tenant_for` gives that scope PRECEDENCE over any SDK). A fresh
    control-plane read for the data half is therefore a SECOND instant: inside
    the resolver cache's 60 s TTL it answers the PROMOTED namespace while the
    tenant still names the DEMOTED graph — so the capture writes one graph and
    files every Object / Event / `-closed` row under the other, the
    `derived != measured` split #10025 exists to remove, on the lane agents
    write through.

    FAIL-ON: hydrate from a fresh read (the shape this test was written
    against) and the WRITE half names `org_{TEAM}_{NEW_ID}` while the FILED
    half names OLD_ID — the two assertions below disagree.
    """
    import asyncio

    from tortoise import mcp_auth, record_writer

    _hosted(monkeypatch)
    fake = _fake_org_row(monkeypatch, f"org_{TEAM}")       # NOT promoted yet
    # What the request was ADMITTED with: ONE resolved credential dict, whose
    # two halves the MCP middleware stamps together (`_current_graph_namespace`
    # and the #9192 tenant scope).
    admitted = {"org_id": TEAM, "graph_id": None,
                "graph_namespace": f"org_{TEAM}", "default_graph_id": OLD_ID}
    graphs_by_namespace = {f"org_{TEAM}": OLD_ID,
                           f"org_{TEAM}_{NEW_ID}": NEW_ID}

    token = mcp_auth._current_graph_namespace.set(admitted["graph_namespace"])
    try:
        # The promotion commits NOW — inside the resolver's 60 s TTL.
        fake.rpc("promote_default_graph",
                 {"p_org_id": TEAM, "p_graph_id": NEW_ID},
                 representation=True)
        # A FRESH read sees the move. That is the second instant the fix must
        # not straddle, and the reason the assertion below is not vacuous.
        assert ha._org_default_namespace(TEAM) == f"org_{TEAM}_{NEW_ID}"

        hydrated = asyncio.run(ha._hydrate_default_namespace({"org_id": TEAM}))
        tenant = record_writer.record_tenant_for(admitted,
                                                control_plane_ids=True)
    finally:
        mcp_auth._current_graph_namespace.reset(token)

    assert tenant == (TEAM, OLD_ID), "the ambient tenant is the admitted one"
    data_ns = ha._data_graph_name(hydrated)
    assert graphs_by_namespace[data_ns] == tenant[1], (
        "the graph the capture WRITES and the graph its record rows are FILED "
        f"under must be ONE graph: write={data_ns!r} "
        f"({graphs_by_namespace[data_ns]!r}) vs filed-under={tenant[1]!r}")

    # ── …and the same rule the other way: once the credential is re-resolved
    # (after the TTL) BOTH halves move together. The fix must not pin the
    # capture to the demoted graph.
    refreshed = {**admitted, "graph_namespace": f"org_{TEAM}_{NEW_ID}",
                 "default_graph_id": NEW_ID}
    token = mcp_auth._current_graph_namespace.set(refreshed["graph_namespace"])
    try:
        hydrated = asyncio.run(ha._hydrate_default_namespace({"org_id": TEAM}))
        tenant = record_writer.record_tenant_for(refreshed,
                                                control_plane_ids=True)
    finally:
        mcp_auth._current_graph_namespace.reset(token)
    assert graphs_by_namespace[ha._data_graph_name(hydrated)] == tenant[1] \
        == NEW_ID


def test_the_capture_admission_key_names_the_graph_the_capture_opens(monkeypatch):
    """The in-flight capture KEY and the capture's OPEN must describe ONE graph.

    `_capture_session_key` scopes an in-flight capture by tenant AND by "the
    graph the capture will ACTUALLY write to" (#3365). The MCP
    `tortoise_session_capture` tool hands the impl a dict with no
    `graph_namespace` (it is frozen — `tools/surface-guard.py`), and the WRITE
    resolves that field inside `_hydrate_default_namespace`; the KEY is computed
    before that hydration, so it must resolve the same value from the same
    source or the two describe different graphs.

    FAIL-ON: keying the bare dict on a PROMOTED org lands the key in the DEMOTED
    graph's bucket (`org_{org_id}`) while the capture writes the promoted one.
    Both halves of the #3365 class follow: a FALSE 409 (a graph-bound key bound
    to the demoted graph shares that bucket, so a legitimate capture of another
    graph is refused) and a reopened #3129 SILENT-LOSS window (one session
    captured through two lanes on ONE physical graph keys into two buckets, so
    both pass admission and the second is served a 0-extract replay).
    """
    from tortoise import mcp_auth

    _hosted(monkeypatch)

    mcp_wide = {"org_id": TEAM}                    # what the MCP tool builds
    rest_wide = {"org_id": TEAM,                   # what the REST seam builds
                 "graph_namespace": f"org_{TEAM}_{NEW_ID}"}
    bound_demoted = {"org_id": TEAM, "graph_id": OLD_ID,
                     "graph_namespace": f"org_{TEAM}"}
    bound_custom = {"org_id": TEAM, "graph_id": OTHER_ID,
                    "graph_namespace": f"org_{TEAM}_{OTHER_ID}"}

    # The credential this request was ADMITTED with is the PROMOTED one.
    token = mcp_auth._current_graph_namespace.set(f"org_{TEAM}_{NEW_ID}")
    try:
        mcp_key = ha._capture_session_key(mcp_wide, "sess")
        rest_key = ha._capture_session_key(rest_wide, "sess")
        custom_key = ha._capture_session_key(bound_custom, "sess")
    finally:
        mcp_auth._current_graph_namespace.reset(token)

    # (1) key == write. The org-wide MCP capture writes the PROMOTED graph, and
    #     its key says so — so it lands in the SAME bucket as the equivalent
    #     org-wide REST credential, which is the graph two lanes must share.
    assert mcp_key == f"{TEAM}:org_{TEAM}_{NEW_ID}:sess", (
        "the admission key must name the graph the capture will WRITE "
        f"(the promoted default), got {mcp_key!r}")
    assert mcp_key == rest_key, (
        "two org-wide captures of one session on the SAME physical graph must "
        f"share one bucket — got {mcp_key!r} vs {rest_key!r}")

    # (2) NO FALSE 409: a graph-bound key bound to the DEMOTED graph is a
    #     DIFFERENT graph, so it must not collide with the promoted capture.
    assert ha._capture_session_key(bound_demoted, "sess") != mcp_key, (
        "a demoted-graph bucket must not refuse a promoted-graph capture")

    # (3) NON-VACUITY: with NOTHING carried (a transport that resolved no
    #     credential) the same dict keeps the historical bucket, so (1) is
    #     discriminating rather than a constant, and no org is moved by accident.
    assert ha._capture_session_key(mcp_wide, "sess") == \
        f"{TEAM}:org_{TEAM}:sess"

    # (4) NOT WIDENED onto a custom graph: a GRAPH-BOUND key answers with ITS
    #     own namespace whatever the ambient credential is — the #2023 guard
    #     that keeps org-level state out of a caller's own graph.
    assert custom_key == f"{TEAM}:org_{TEAM}_{OTHER_ID}:sess", custom_key


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
    token = ha._resolved_default_namespace.set(None)
    try:
        ha._publish_default_namespace(_credential())
        ha._org_sdk(TEAM)                   # e.g. _org_proj / _graph_available
        assert opened == [{"graph_name": f"org_{TEAM}_{NEW_ID}"}]

        # The hint is ORG-scoped: a different org in the same context falls back
        # to the read (here unreadable → the historical open), never to this
        # org's graph.
        opened.clear()
        ha._org_sdk(OTHER_TEAM)
        assert opened == [{"namespace": OTHER_TEAM}]
    finally:
        ha._resolved_default_namespace.reset(token)


def test_a_graph_bound_credential_publishes_no_default_namespace(monkeypatch):
    """A graph-bound credential's `graph_namespace` is a CUSTOM graph's — and it
    passes the `org_{org_id}_` shape test, so a "default graph" consumer that
    took it would evaluate and write org-level state (the capture completion
    gate, the onboarding projection) into the caller's own graph while every
    org-level reader looked at the org's default.

    FAIL-ON: publish unconditionally and `_org_default_namespace` answers the
    caller's custom namespace for a never-promoted org — the R1 regression, on
    the graph-bound capture path (`POST /v1/sessions` admits those keys, and
    `_maybe_apply_completion` runs on every capture)."""
    _hosted(monkeypatch)
    opened = _capture_make_sdk(monkeypatch)
    fake = _fake_org_row(monkeypatch, f"org_{TEAM}")   # NEVER promoted

    token = ha._resolved_default_namespace.set(None)
    try:
        ha._publish_default_namespace(
            _credential(graph_id=NEW_ID,
                        graph_namespace=f"org_{TEAM}_{NEW_ID}"))
        # Nothing published for a bound credential → the org's default is read,
        # not the caller's binding.
        assert ha._resolved_default_namespace.get() is None
        ha._org_sdk(TEAM)
        assert opened == [{"namespace": TEAM}], (
            "an org-level lane must open the org's DEFAULT graph, never the "
            "graph-bound credential's own graph")
    finally:
        ha._resolved_default_namespace.reset(token)
    assert fake is not None


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


# ── 14. The lanes that build their OWN SDK open and file from ONE read ─────
# The five lanes below resolve the graph they OPEN (the org's default
# namespace) and the tenant they FILE UNDER (that same row's id). Asked as two
# reads, the pair is two instants — and `promote_default_graph` commits both
# facts in one transaction, so a promotion landing between them leaves the job
# writing the graph the promotion DEMOTED while every Object / Event / `-closed`
# row it mints names the graph the promotion PROMOTED. That is the
# `derived != measured` split #10025 exists to remove, re-created by the verb
# #10180 adds. `_org_default_graph` answers both from ONE `public.graphs` read.

_TWO_ANSWER_LANES = ("_run_indexing", "_run_docs_indexing",
                     "_run_onboarding_seed", "_run_starter_seed",
                     "_relink_sessions_after_index")


def test_the_default_graph_pair_is_read_at_one_instant(monkeypatch):
    """A promotion committing between the two reads SPLITS a lane; one read
    cannot.

    The race is driven deterministically: the fake fires a real
    `promote_default_graph` immediately after answering the FIRST `graphs` read
    — i.e. exactly between read #1 (name) and read #2 (id) of the pre-fix shape.

    FAIL-ON (measured by the non-vacuity half below): with the pair resolved by
    two reads, the namespace says "not moved" (`namespace=org_id`, the DEMOTED
    graph) while the tenant says NEW_ID (the PROMOTED graph) — the lane then
    writes one graph and files the rows under the other, permanently.
    """
    fake = _fake_org_row(monkeypatch, f"org_{TEAM}")
    orig_query = fake.query
    fired: list[bool] = []
    def _query(table, **kw):
        rows = orig_query(table, **kw)
        # Fire on the FIRST control-plane read of EITHER table: the interesting
        # instant is "a promotion commits after the first answer and before a
        # second could be asked". One read ⇒ both answers are from it; two reads
        # ⇒ the name is pre-promotion and the id is post.
        if table in ("organizations", "graphs") and not fired:
            fired.append(True)
            # The promotion commits NOW — after this read answered.
            fake.rpc("promote_default_graph",
                     {"p_org_id": TEAM, "p_graph_id": NEW_ID},
                     representation=True)
        return rows

    monkeypatch.setattr(fake, "query", _query)
    token = ha._resolved_default_namespace.set(None)
    try:
        moved, tenant = ha._org_default_graph(TEAM)
    finally:
        ha._resolved_default_namespace.reset(token)
    assert fired, "the fake never served a `graphs` read — nothing was raced"
    assert (moved, tenant) in ((None, OLD_ID),
                               (f"org_{TEAM}_{NEW_ID}", NEW_ID)), (
        "the namespace a lane OPENS and the tenant it FILES UNDER must describe "
        "the same default row: got "
        f"{(moved, tenant)!r} — that pair is a graph/data split (#10025)")

    # ── NON-VACUITY: the PRE-FIX two-read shape still splits on this fixture,
    # so the assertion above is discriminating rather than descriptive. Rebuilt
    # on a fresh control plane because the promotion above already moved it.
    from tortoise.supabase_control import default_graph_id_for

    two = _fake_org_row(monkeypatch, f"org_{TEAM}")
    name_before = ha._org_default_namespace(TEAM)          # read #1
    two.rpc("promote_default_graph",
            {"p_org_id": TEAM, "p_graph_id": NEW_ID},
            representation=True)                          # the commit
    tenant_after = default_graph_id_for(two, TEAM)        # read #2
    assert (name_before, tenant_after) == (None, NEW_ID), (
        "the fixture no longer reproduces the split — the test above would be "
        f"vacuous. got {(name_before, tenant_after)!r}")


def test_the_bind_takes_a_pre_resolved_tenant_without_reading(monkeypatch):
    """`default_graph_id=` skips the read, including when it is ``None``.

    FAIL-ON: ignore the pre-resolved id and the lane pays a SECOND read for an
    answer it already has — the read that straddles the promotion in the test
    above. A ``None`` must also skip the read: it is "no row was nameable, bind
    nothing" (fail-closed `no_tenant`), not "go read and hope".
    """
    _hosted(monkeypatch)

    class _Boom:
        def query(self, *a, **k):
            pytest.fail("a pre-resolved tenant must not read the control plane")

    monkeypatch.setattr("tortoise.supabase_control.get_control_plane",
                        lambda: _Boom())
    installed: list[tuple] = []

    class _Sdk:
        def _bind_record_tenant(self, *a):
            installed.append(a)

    ha._bind_default_graph_record_tenant(_Sdk(), TEAM, default_graph_id=NEW_ID)
    assert installed == [(TEAM, NEW_ID)], installed
    ha._bind_default_graph_record_tenant(_Sdk(), TEAM, default_graph_id=None)
    assert installed == [(TEAM, NEW_ID)], (
        "a None pre-resolved id must bind NOTHING (never a guess)")


def test_the_default_graph_pair_comes_from_the_request_own_credential(monkeypatch):
    """`_org_default_graph`'s HINT branch — the REQUEST path.

    A lane reached from an authenticated request resolves the pair from the
    credential that request was ADMITTED with (`_publish_default_namespace`), so
    it pays no control-plane read and cannot disagree with the graph the data
    plane opened for the same request. This is the branch the other pair tests
    do NOT take: `test_the_default_graph_pair_is_read_at_one_instant` clears the
    contextvar first, so it pins the READ shape only, and the hint branch (the
    one every request-path lane traverses) had no direct test.

    FAIL-ON: drop the hint branch and a request admitted against the PROMOTED
    graph resolves the DEMOTED one — the graph/data split `_org_default_graph`
    exists to remove — which the non-vacuity half below measures on this very
    fixture (the read answers `(None, OLD_ID)` where the credential said
    `(org_{TEAM}_{NEW_ID}, NEW_ID)`).
    """
    _hosted(monkeypatch)
    fake = _fake_org_row(monkeypatch, f"org_{TEAM}")   # org row: NEVER promoted
    reads: list[str] = []
    orig_query = fake.query

    def _query(table, **kw):
        reads.append(table)
        return orig_query(table, **kw)

    monkeypatch.setattr(fake, "query", _query)
    token = ha._resolved_default_namespace.set(None)
    try:
        ha._publish_default_namespace(_credential(default_graph_id=NEW_ID))
        assert ha._org_default_graph(TEAM) == (f"org_{TEAM}_{NEW_ID}", NEW_ID)
        assert reads == [], (
            "a request-resolved credential must not cost a control-plane read — "
            f"the pair came from the credential, yet {reads!r} was read")

        # The hint is ORG-scoped: another org in the same context must read its
        # own answer, never inherit this org's promoted graph.
        reads.clear()
        assert ha._org_default_graph(OTHER_TEAM) == (None, None)
        assert reads, "a different org must not be served by this org's hint"
    finally:
        ha._resolved_default_namespace.reset(token)

    # ── NON-VACUITY: with the hint cleared, this fixture's read path answers
    # the DEMOTED pair — so the assertions above are discriminating rather than
    # descriptive. (One instant per answer; two statements are two instants,
    # which is why the pair comes from one row or from one credential.)
    assert ha._org_default_graph(TEAM) == (None, OLD_ID)


def test_every_default_graph_lane_resolves_both_answers_once():
    """Source pin for the CLASS, AST-based (not a grep of one lane).

    Each of the five lanes that build their own SDK must resolve the pair
    through `_org_default_graph` — and hand the tenant to the bind — so a new
    lane that copies `_org_default_namespace(...)` + `_bind_default_graph_record_tenant(...)`
    (the pre-fix spelling, which reds the test above) cannot pass unnoticed.

    FAIL-ON: reintroduce the two-read spelling in any of these lanes and this
    names it.
    """
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).resolve().parent.parent / "tortoise"
                      / "hosted_api.py").read_text(encoding="utf-8"))
    fns = {n.name: n for n in ast.walk(tree)
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for lane in _TWO_ANSWER_LANES:
        assert lane in fns, f"{lane} is gone — update this pin's inventory"
        calls = [c for c in ast.walk(fns[lane]) if isinstance(c, ast.Call)]
        callees = {getattr(c.func, "id", None) for c in calls}
        assert "_org_default_graph" in callees, (
            f"{lane} no longer resolves the default graph's name AND tenant "
            "from one read (`_org_default_graph`) — two reads straddle a "
            "promotion and split the graph it writes from the rows it files")
        assert "_org_default_namespace" not in callees, (
            f"{lane} still resolves the default NAMESPACE on its own — that is "
            "the read the promotion straddles; take the pair from "
            "`_org_default_graph`")
        binds = [c for c in calls
                 if getattr(c.func, "id", None) ==
                 "_bind_default_graph_record_tenant"]
        assert binds, f"{lane} no longer binds the default-graph tenant"
        assert any(k.arg == "default_graph_id" for k in binds[0].keywords), (
            f"{lane} binds without handing over the tenant it just read — the "
            "bind then READS again, which is the split")


# ── 15. No SDK is constructed inside a CONTROL-PLANE worker pool (#7500) ────
# `_cp_offload` submits to a POOLED worker (`control_plane_worker(pool).submit`)
# WITHOUT copying the caller's context (cpython#78195), so an `_make_sdk` built
# there resolves its store path from the thread's stale INHERITED stamp once the
# pool reuses it — the job then writes a store the test's reader never looks at
# (tortoise#7500, measured as 3 reds under #4645). The READ may ride the worker;
# the CONSTRUCTION stays on the loop. `_graph_offload` and `asyncio.to_thread`
# DO copy the caller's context (`contextvars.copy_context()`), which is why
# `_data_sdk`'s construction is legitimately off-loaded through them.

_CP_WORKER_SDK_BUILDERS = ("_make_sdk", "_org_sdk", "_org_sdk_from_name",
                           "_org_sdk_for_credential", "_data_sdk")


def test_no_sdk_is_constructed_inside_a_control_plane_worker():
    """AST pin over EVERY `_cp_offload` call in `hosted_api`.

    FAIL-ON: `sdk = await _cp_offload(lambda: _org_sdk(org_id), op="org_sdk")`
    — the spelling this PR shipped in `import_org` — builds `_make_sdk` inside
    a pooled worker, i.e. the #7500 shape whose measured signature is a job
    writing a store its reader never reads, and it is precisely the placement
    `_org_sdk_from_name`'s docstring forbids while the other lanes (the
    indexing runners) get it right. The fix off-loads only the READ
    (`_org_default_namespace`) and builds with `_org_sdk_from_name` on the loop.
    """
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).resolve().parent.parent / "tortoise"
                      / "hosted_api.py").read_text(encoding="utf-8"))

    def _callee(node):
        return getattr(node, "id", None) or getattr(node, "attr", None)

    scanned = 0
    offenders: list[tuple[str, str]] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call in ast.walk(fn):
            if not isinstance(call, ast.Call) \
                    or _callee(call.func) != "_cp_offload":
                continue
            scanned += 1
            built = sorted({_callee(n.func) for n in ast.walk(call)
                            if isinstance(n, ast.Call)}
                           & set(_CP_WORKER_SDK_BUILDERS))
            if built:
                offenders.append((fn.name, ",".join(built)))
    assert scanned >= 40, (
        f"only {scanned} `_cp_offload` calls were scanned — this pin is not "
        "seeing the surface it guards (an empty/vacuous match would pass)")
    assert not offenders, (
        f"{offenders} construct an SDK inside `_cp_offload`'s POOLED worker. "
        "That pool does not copy the context, so `_make_sdk` resolves its store "
        "path from the thread's stale stamp and the job writes a store its "
        "reader never reads (#7500). Off-load the READ and build on the loop "
        "with `_org_sdk_from_name`.")


# ── 9. The org's onboarding record follows the move (#10180, review P2) ────
#
# :OnboardingState + its COMPLETED_STEP edges are the ONE org-level record kept
# INSIDE a default graph, and every org-level reader in this change now opens
# the PROMOTED default. A move that left the record behind serves
# `flow_defaults()` over an org that had completed — onboarding re-opens, the
# tool group is re-exposed to agents, the `fork`/`compact` choice is lost — so
# the verb carries it and reports the outcome.

_OLD_NS = f"org_{TEAM}"
_NEW_NS = f"org_{TEAM}_{NEW_ID}"
_COMPLETE_STEPS = ("team-named", "harness-connected", "first-points-filed",
                   "decide-completed")


def _onboarding_state():
    from tortoise.onboarding import state as _os
    return _os


def _seed_onboarding(namespace: str, *, steps=_COMPLETE_STEPS,
                     status: str | None = None, fork: str | None = None,
                     compact: bool = False) -> None:
    """Write a REAL OnboardingState into `namespace` (the embedded lane)."""
    _os = _onboarding_state()
    sdk = ha._make_sdk(graph_name=namespace)
    try:
        proj = sdk._get_proj()
        _os.ensure_onboarding_state_node(proj, TEAM, fork=fork, compact=compact)
        for step in steps:
            _os.write_completed_step(proj, TEAM, step)
        if status is not None:
            _os.write_status(proj, TEAM, status)
    finally:
        sdk.close()


def _read_onboarding(namespace: str) -> dict | None:
    _os = _onboarding_state()
    sdk = ha._make_sdk(graph_name=namespace)
    try:
        proj = sdk._get_proj()
        node = _os.read_onboarding_node(proj, TEAM)
        return None if node is None else {
            **node, "completed_steps": _os.completed_steps(proj, TEAM)}
    finally:
        sdk.close()


def test_the_promotion_carries_the_orgs_onboarding_record(sb):
    """FAIL-ON (the P2 this pins): the record stays in the DEMOTED graph, so the
    projection — which now opens the promoted default — serves `flow_defaults()`
    and `onboarding_complete` falls back to the legacy jsonb flag, i.e. a
    promotion silently re-opens onboarding for an org the gate documents as
    MONOTONIC."""
    tc, _, _ = sb
    _seed_onboarding(_OLD_NS, steps=_COMPLETE_STEPS, status="complete",
                     fork="build")
    before = ha._get_onboarding_projection(TEAM)
    assert before["onboarding_complete"] is True, before
    assert before["fork"] == "build"

    _as_session()
    r = tc.post(f"/v1/graphs/{NEW_ID}/promote?org_id={TEAM}")
    assert r.status_code == 200, r.text
    carried = r.json()["onboarding_state"]
    assert carried == {"migrated": True, "reason": "copied", "from": _OLD_NS,
                       "to": _NEW_NS, "steps": len(_COMPLETE_STEPS),
                       "status": "complete"}, carried

    # The new default graph really holds the record ...
    node = _read_onboarding(_NEW_NS)
    assert node is not None
    assert node["status"] == "complete"
    assert node["fork"] == "build"
    assert sorted(node["completed_steps"]) == sorted(_COMPLETE_STEPS)

    # ... and the surface the agent/dashboard read follows it, unchanged.
    after = ha._get_onboarding_projection(TEAM)
    assert after["onboarding_complete"] is True, after
    assert after["status"] == "complete"
    assert after["fork"] == "build"
    assert sorted(after["completed_steps"]) == sorted(_COMPLETE_STEPS)
    # The old graph is untouched: the move copies, it does not steal.
    assert _read_onboarding(_OLD_NS) is not None


def test_the_carry_is_a_union_so_a_partial_record_never_regresses(sb):
    """A target that already holds a node keeps the more-advanced state: status
    is monotonic (`complete` never regresses) and steps only ever GAIN."""
    tc, _, _ = sb
    _seed_onboarding(_OLD_NS, steps=_COMPLETE_STEPS, status="complete")
    _seed_onboarding(_NEW_NS, steps=("harness-connected",), fork="build")

    _as_session()
    r = tc.post(f"/v1/graphs/{NEW_ID}/promote?org_id={TEAM}")
    assert r.status_code == 200, r.text
    node = _read_onboarding(_NEW_NS)
    assert node["status"] == "complete", node
    assert set(node["completed_steps"]) >= set(_COMPLETE_STEPS)


def test_the_promotion_of_an_org_that_never_onboarded_says_so(sb):
    """No node in the previous default → nothing to carry, and the response says
    `absent` rather than implying a migration that never happened."""
    tc, _, _ = sb
    _as_session()
    r = tc.post(f"/v1/graphs/{NEW_ID}/promote?org_id={TEAM}")
    assert r.status_code == 200, r.text
    assert r.json()["onboarding_state"] == {
        "migrated": False, "reason": "absent", "from": _OLD_NS,
        "to": _NEW_NS, "steps": 0, "status": None}
    assert _read_onboarding(_NEW_NS) is None


def test_re_promoting_the_current_default_repairs_a_stranded_record(
        sb, monkeypatch):
    """The pre-fix state, reproduced: a move whose carry did not run leaves the
    record behind and the projection loses it (asserted FIRST — that is the
    non-vacuity half). Re-promoting the CURRENT default is the repair lever: the
    idempotent arm carries from the listed historical name."""
    tc, _, _ = sb
    _seed_onboarding(_OLD_NS, steps=_COMPLETE_STEPS, status="complete")
    real_carry = ha._carry_onboarding_state
    monkeypatch.setattr(ha, "_carry_onboarding_state", lambda *_a, **_k: {
        "migrated": False, "reason": "carry_failed", "from": None,
        "to": None, "steps": 0, "status": None})
    _as_session()
    r = tc.post(f"/v1/graphs/{NEW_ID}/promote?org_id={TEAM}")
    assert r.status_code == 200, r.text
    assert r.json()["onboarding_state"]["reason"] == "carry_failed"
    stranded = ha._get_onboarding_projection(TEAM)
    assert stranded["completed_steps"] == [], stranded
    assert stranded["onboarding_complete"] is False, stranded

    monkeypatch.setattr(ha, "_carry_onboarding_state", real_carry)
    r = tc.post("/v1/graphs/default/promote?org_id=" + TEAM)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["already_default"] is True
    assert body["onboarding_state"]["migrated"] is True
    assert body["onboarding_state"]["from"] == _OLD_NS
    repaired = ha._get_onboarding_projection(TEAM)
    assert repaired["onboarding_complete"] is True, repaired
    assert sorted(repaired["completed_steps"]) == sorted(_COMPLETE_STEPS)

    # Idempotent in the other direction: the node is now PRESENT, so a further
    # promote writes nothing and says so.
    r = tc.post("/v1/graphs/default/promote?org_id=" + TEAM)
    assert r.json()["onboarding_state"]["reason"] == "already_present"
    assert _read_onboarding(_NEW_NS)["status"] == "complete"


def test_the_existence_gate_follows_a_promoted_default(monkeypatch):
    """The projection's existence verdict is about the org's DEFAULT namespace,
    not `org_{org_id}`: the demoted graph is an ordinary custom graph and can be
    deleted, and keying on the historical name would then serve `flow_defaults`
    over a present graph."""
    _hosted(monkeypatch)
    monkeypatch.setattr(ha, "_org_default_namespace", lambda _oid: _NEW_NS)
    assert ha._graph_has_org_namespace(TEAM, listed={_NEW_NS}) is True
    assert ha._graph_has_org_namespace(TEAM, listed={_OLD_NS}) is False
    # An unpromoted org keeps the historical answer (both tenant prefixes).
    monkeypatch.setattr(ha, "_org_default_namespace", lambda _oid: None)
    assert ha._graph_has_org_namespace(TEAM, listed={_OLD_NS}) is True
    assert ha._graph_has_org_namespace(TEAM, listed={f"team_{TEAM}"}) is True
    assert ha._graph_has_org_namespace(TEAM, listed={_NEW_NS}) is False
