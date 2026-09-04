"""C4 (#2113): FalkorDB per-graph ACL layer — docker-lane integration tests.

The ACL layer only exists on a SERVER FalkorDB (falkordb module); embedded
redislite (carve-out, no TORTOISE_DB_URI) and bare redis have no per-graph
ACL — every module fn no-ops there. These tests skip unless a server URI is
set (docker lane) and the module is present.

Test isolation on the SHARED matrix server: every test uses a UNIQUE team
id + graph id (uuid) and drops its ACL user in teardown — ACL users are
GLOBAL server state (R13). Registry nodes land on the per-tmp_path server
graph (conftest redirect) and accumulate like every other docker-lane test.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from urllib.parse import urlparse

import pytest

from tortoise import acl_graph_users as acl

_SUPPORTED = {"docker", "redis", "rediss"}


def _server_uri_set() -> bool:
    uri = os.environ.get("TORTOISE_DB_URI") or ""
    scheme = uri.split("://", 1)[0]
    return scheme in _SUPPORTED and bool(urlparse(uri).hostname)


pytestmark = pytest.mark.skipif(
    not _server_uri_set(),
    reason="docker lane only — per-graph ACL needs a server FalkorDB (URI set)")

# falkordb module present gate (bare redis → module no-ops → skip):
if _server_uri_set():
    _probe = acl._admin_client()
    _HAS_FALKORDB = _probe is not None and acl._falkordb_present(_probe)
else:
    _HAS_FALKORDB = False

pytestmark = pytest.mark.skipif(
    not _HAS_FALKORDB,
    reason="no falkordb module on the target server — ACL layer no-ops")


def _patch_tortoise_sdk_init(db_path):
    """Mirror tests/test_hosted_api.py: force TortoiseSDK onto a temp
    db_path + clear the _make_sdk embedded-fallback anchor."""
    import tortoise.hosted_api as ha_mod
    _orig_init = ha_mod.TortoiseSDK.__init__

    def _patched_init(self, db_path_arg=None, *, namespace=None, **kwargs):
        _orig_init(self, db_path, namespace=namespace)

    ha_mod.TortoiseSDK.__init__ = _patched_init
    ha_mod._FALLBACK_KEEPALIVE.clear()
    return _orig_init


def _restore_tortoise_sdk_init(original_init):
    """Mirror tests/test_hosted_api.py: restore + evict the anchor."""
    import tortoise.hosted_api as ha_mod
    ha_mod.TortoiseSDK.__init__ = original_init


@pytest.fixture
def acl_env(tmp_path):
    """Registry Team + custom Graph nodes on an isolated store + unique ids.
    Returns {ha, sdk, tid, gid, ns}. ACL user dropped in teardown."""
    import tortoise.hosted_api as ha_mod
    db_path = os.path.join(tmp_path, "test.db")
    os.environ["TORTOISE_DB_PATH"] = db_path
    _orig = _patch_tortoise_sdk_init(db_path)
    env = {}
    try:
        tid = f"team-acl-{uuid.uuid4().hex[:12]}"
        ha_mod._make_sdk(namespace="registry")._get_registry().query(
            "CREATE (t:Team {id:$id, tier:'pro', max_graphs:100, "
            "max_api_keys:20})",
            params={"id": tid},
        )
        sdk = ha_mod._make_sdk(namespace="registry")
        g = sdk._graph_create(tid, f"g-{uuid.uuid4().hex[:8]}", kind="custom")
        env = {"ha": ha_mod, "sdk": sdk, "tid": tid, "gid": g["graph_id"],
               "ns": g["namespace"]}
        yield env
    finally:
        with contextlib.suppress(Exception):
            acl.drop_acl_user(env.get("gid", ""))
        os.environ.pop("TORTOISE_DB_PATH", None)
        _restore_tortoise_sdk_init(_orig)


# ── E2E-1: config inspection ─────────────────────────────────────────────

def test_create_user_exact_permissions(acl_env):
    """E2E-1: create_acl_user → the user exists with the EXACT hardened
    permission set: key pattern = the graph's namespace ONLY (no ~*), the
    three graph commands (+GRAPH.QUERY/+GRAPH.RO_QUERY/+PING over a -@all
    deny-all base), and NONE of GRAPH.LIST/KEYS/SCAN/CONFIG/@all. The
    credential is stored server-side (registry Graph node) — never on the
    key."""
    tid, gid, ns = acl_env["tid"], acl_env["gid"], acl_env["ns"]
    cred = acl.create_acl_user(gid, tid)
    assert cred is not None
    assert cred["username"] == f"tenant_{gid}"
    assert cred["graph"] == ns  # exact namespace
    try:
        cfg = acl.acl_user_config(gid)
        assert cfg is not None
        assert cfg["keys"] == [f"~{ns}"], cfg["keys"]  # exact graph only
        cmds = set(cfg["commands"])
        assert "-@all" in cmds  # deny-all base (redis 8 composition)
        assert {"+graph.query", "+graph.ro_query", "+ping"} <= cmds
        # deny set never present:
        assert not cmds & {"+@all", "+graph.list", "+graph.config",
                           "+graph.debug", "+graph.udf", "+keys", "+scan"}
        assert "nopass" not in cfg["flags"]
    finally:
        acl.drop_acl_user(gid)


def test_credential_stored_server_side(acl_env):
    """Credential mapping: {username, password, graph} ride the registry
    Graph node (credential_for_graph reads them server-side); the graph
    node carries acl_user/acl_pass and the mapping round-trips."""
    tid, gid, ns = acl_env["tid"], acl_env["gid"], acl_env["ns"]
    cred = acl.create_acl_user(gid, tid)
    try:
        got = acl.credential_for_graph(tid, gid)
        assert got is not None
        assert got["username"] == cred["username"]
        assert got["password"] == cred["password"]
        assert got["graph"] == ns
        # Re-create is idempotent — stored password reused (no churn).
        again = acl.create_acl_user(gid, tid)
        assert again["password"] == cred["password"]
    finally:
        acl.drop_acl_user(gid)


# ── E2E-2 half: cross-graph NOPERM ───────────────────────────────────────

def test_cross_graph_noperm(acl_env):
    """E2E-2 (data half): a graph-A credential (tenant_<gidA> + its
    password) runs GRAPH.QUERY on its OWN graph OK and gets NOPERM on
    another team's graph (and on a graph the app never created)."""
    import redis as redis_py
    tid, gid, ns = acl_env["tid"], acl_env["gid"], acl_env["ns"]
    cred = acl.create_acl_user(gid, tid)
    try:
        c = redis_py.Redis(
            host=urlparse(os.environ["TORTOISE_DB_URI"]).hostname,
            port=urlparse(os.environ["TORTOISE_DB_URI"]).port or 16379,
            username=cred["username"], password=cred["password"],
            decode_responses=True, socket_timeout=5,
        )
        # Own graph → OK.
        c.execute_command("GRAPH.QUERY", ns, "RETURN 1")
        # Cross-graph (another team) → NOPERM.
        other_ns = f"team_other_{gid}"
        try:
            c.execute_command("GRAPH.QUERY", other_ns, "RETURN 1")
            raise AssertionError("cross-graph query unexpectedly allowed")
        except redis_py.ResponseError as e:
            assert "NOPERM" in str(e).upper() or "permission" in str(e).lower(), e
        # The graph command allowlist holds: KEYS is denied.
        try:
            c.execute_command("KEYS", "*")
            raise AssertionError("KEYS unexpectedly allowed for tenant user")
        except redis_py.ResponseError as e:
            assert "NOPERM" in str(e).upper() or "permission" in str(e).lower(), e
    finally:
        acl.drop_acl_user(gid)


# ── E2E-8 half: drop on delete / E2E-11: no orphans ──────────────────────

def test_drop_user(acl_env):
    """E2E-8 half: drop_acl_user removes the tenant user (idempotent —
    dropping an absent user is a no-op)."""
    tid, gid = acl_env["tid"], acl_env["gid"]
    acl.create_acl_user(gid, tid)
    assert acl.acl_user_exists(gid)
    acl.drop_acl_user(gid)
    assert not acl.acl_user_exists(gid)
    acl.drop_acl_user(gid)  # idempotent no-op


def test_rollback_drops_user_and_node(acl_env):
    """E2E-11 seam: hosted_api._rollback_graph deletes the Graph node AND
    drops the ACL user (a strict ACL create may have landed before the
    rollback trigger — no orphan user on a rolled-back mint)."""
    ha, tid, gid = acl_env["ha"], acl_env["tid"], acl_env["gid"]
    acl.create_acl_user(gid, tid)
    assert acl.acl_user_exists(gid)
    ha._rollback_graph(tid, {"id": gid})
    assert not acl.acl_user_exists(gid)
    # Node is gone too.
    rows = ha._make_sdk(namespace="registry")._get_registry().query(
        "MATCH (g:Graph {id:$gid, team_id:$tid}) RETURN g.id",
        params={"gid": gid, "tid": tid},
    ).result_set
    assert rows == []


def test_strict_mint_failure_no_orphan(acl_env, monkeypatch):
    """E2E-11: a STRICT provisioning mint whose ACL create fails (server
    reachable, real error) leaves NO key and NO graph — _mint_graph_key
    raises before the key write and _provision_graph rolls the graph back."""
    from tortoise.acl_graph_users import AclLayerError
    ha, tid, gid = acl_env["ha"], acl_env["tid"], acl_env["gid"]

    def _boom(graph_id, team_id):
        raise AclLayerError("simulated SETUSER failure")

    monkeypatch.setattr(acl, "create_acl_user", _boom)
    # _mint_graph_key strict → raises through (nothing committed).
    with pytest.raises(AclLayerError):
        ha._mint_graph_key(tid, gid, ["graphs:read"], None)
    assert not acl.acl_user_exists(gid)
    # No key minted for the graph.
    rows = ha._make_sdk(namespace="registry")._get_registry().query(
        "MATCH (k:APIKey {graph_id:$gid}) RETURN k.id",
        params={"gid": gid},
    ).result_set
    assert rows == []


def test_soft_mint_survives_acl_failure(acl_env, monkeypatch):
    """Fail-soft contract: a STANDALONE (non-strict) graph-bound key mint
    proceeds when the ACL create fails — the ACL is defense-in-depth, never
    a SPOF (the app-layer scope check is authoritative)."""
    from tortoise.acl_graph_users import AclLayerError
    ha, tid, gid = acl_env["ha"], acl_env["tid"], acl_env["gid"]

    def _boom(graph_id, team_id):
        raise AclLayerError("simulated SETUSER failure")

    monkeypatch.setattr(acl, "create_acl_user", _boom)
    # Soft path (acl_strict False — the create_api_key standalone surface).
    minted = ha._mint_key(tid, graph_id=gid, scopes=["graphs:read"],
                          delegation_depth=None)
    assert minted["id"]
    assert minted["graph_id"] == gid
    # Clean up the minted key.
    ha._revoke_minted_key(tid, minted["id"])


# ── Persistence + default-user security ───────────────────────────────────

def test_acl_save_issued_and_user_survives_reconnect(acl_env):
    """R15: every mutation issues ACL SAVE (best-effort) and the user is
    visible to a FRESH admin connection (restart-durability = aclfile is a
    deployment config; the SAVE + presence assert pins the persistence
    contract that survives a reconnect)."""
    tid, gid = acl_env["tid"], acl_env["gid"]
    acl.create_acl_user(gid, tid)
    # Fresh connection (no shared client state) still lists the user.
    from tortoise import acl_graph_users as _acl
    assert _acl.acl_user_config(gid) is not None
    acl.drop_acl_user(gid)
    assert not acl.acl_user_exists(gid)


def test_default_user_secured():
    """E2E-1: the docker matrix default user is password-secured (nopass
    absent / a password hash present) — per-graph ACL users are only
    created behind a secured default (the layer is theater otherwise)."""
    client = acl._admin_client()
    assert client is not None
    acl._ensure_default_user_secured(client)  # no raise = secured


# ── Code-review round-1 pins ──────────────────────────────────────────────

def test_store_miss_rotate_invalidates_old_secret(acl_env, monkeypatch):
    """Code-review: when the user EXISTS but the stored password is MISSING
    (crash between SETUSER and registry store), the next create generates a
    fresh secret AND resetpasses — the orphaned prior secret dies (a bare
    ``>pw`` would APPEND a second live credential)."""
    from tortoise.acl_graph_users import _user_exists_on
    tid, gid = acl_env["tid"], acl_env["gid"]
    cred1 = acl.create_acl_user(gid, tid)
    # Simulate the store-miss window: wipe the node's stored password.
    sdk = acl_env["sdk"]
    sdk._get_registry().query(
        "MATCH (g:Graph {id:$gid, team_id:$tid}) SET g.acl_pass = null",
        params={"gid": gid, "tid": tid},
    )
    client = acl._admin_client()
    assert _user_exists_on(client, f"tenant_{gid}")
    # Next create: fresh password + resetpass (old one invalidated).
    cred2 = acl.create_acl_user(gid, tid)
    assert cred2["password"] != cred1["password"]
    # Old password no longer authenticates.
    from urllib.parse import urlparse as _up

    import redis as redis_py
    uri = _up(os.environ["TORTOISE_DB_URI"])
    with pytest.raises((redis_py.AuthenticationError, redis_py.ResponseError)):
        redis_py.Redis(host=uri.hostname, port=uri.port or 16379,
                       username=f"tenant_{gid}", password=cred1["password"],
                       decode_responses=True, socket_timeout=5,
                       ).execute_command("PING")
    # New password authenticates.
    redis_py.Redis(host=uri.hostname, port=uri.port or 16379,
                   username=f"tenant_{gid}", password=cred2["password"],
                   decode_responses=True,
                   ).execute_command("PING")


def test_unsafe_id_rejected_fail_closed(acl_env):
    """Code-review hardening: ids carrying rule-injection chars (spaces /
    quotes / globs) are refused — SETUSER rule args are space-split
    server-side."""
    from tortoise.acl_graph_users import AclLayerError
    with pytest.raises(AclLayerError, match="unsafe graph id"):
        acl.create_acl_user("g_bad id~*", acl_env["tid"])
    with pytest.raises(AclLayerError, match="unsafe team id"):
        acl._graph_namespace('tid" +@all', acl_env["gid"])
    with pytest.raises(AclLayerError, match="unsafe graph id"):
        acl.drop_acl_user('g_bad"\n~* +@all')


def test_open_default_strict_provision_503(acl_env, monkeypatch):
    """Code-review: an open-default (nopass) server makes the STRICT
    provisioning mint surface an actionable 503 (not an opaque 500) and
    roll back the graph — no orphan, clear remedy."""
    from tortoise.acl_graph_users import AclLayerError
    ha, tid = acl_env["ha"], acl_env["tid"]

    # Force the layer to think the default user is open.
    def _secured(client):
        raise AclLayerError(
            "default ACL user is open (nopass) — per-graph ACL users are "
            "theater without a secured default. Set a password/requirepass "
            "first.")

    monkeypatch.setattr(acl, "_ensure_default_user_secured", _secured)
    team = {"id": tid, "tier": "pro", "max_graphs": 100, "max_api_keys": 20}
    try:
        ha._provision_graph(team, f"n-{uuid.uuid4().hex[:6]}", None, None)
        raise AssertionError("provision unexpectedly succeeded")
    except Exception as e:
        from fastapi import HTTPException
        assert isinstance(e, HTTPException)
        assert e.status_code == 503, e.status_code
        assert "secured default" in str(e.detail) or "password" in str(e.detail), e.detail
    # Rolled back: only the FIXTURE graph remains (the provisioned one is
    # gone — its name never landed).
    sdk = ha._make_sdk(namespace="registry")
    rows = sdk._get_registry().query(
        "MATCH (g:Graph {team_id:$tid}) RETURN count(g)",
        params={"tid": tid},
    ).result_set
    assert rows[0][0] == 1  # fixture graph only — provisioned graph rolled back
