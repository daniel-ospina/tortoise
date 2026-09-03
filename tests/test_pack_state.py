"""#318 — multi-tenant pack isolation tests.

Covers: pack_state activation semantics (idempotent additive MERGE,
provenance-marked PackInstall nodes, env validation, self-heal, concurrency),
the three provisioning hooks (registry /internal/provision, Supabase
provision_team RPC, self-service register), the introspection surfaces
(REST GET /v1/packs + MCP packs_list — auth-only, D6 empty masking,
cross-tenant isolation), and the backfill script's dry-run default.

Runs on embedded FalkorDBLite (no Docker needed) — per-test fresh DB paths.
"""
from __future__ import annotations

import os
import subprocess  # noqa: F401
import sys
import tempfile
import threading
from contextlib import asynccontextmanager

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")
os.environ.setdefault("FASTAPI_INTERNAL_KEY", "test-internal-shared-secret-xyz")

import pytest  # noqa: I001

from tests._http_fixtures import patched_tortoise_sdk
from tortoise.pack_state import (
    DEFAULT_STARTER_PACKS, ensure_tenant_packs, get_tenant_packs,
)
from tortoise.sdk import TortoiseSDK

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# #1719 (Task 3): team_memberships.user_id is a uuid column — real JWT
# subjects are UUIDs; non-UUID user_id literals are prod-impossible.
# api_keys.created_by stays TEXT and remains non-UUID.
_U1 = "9f2c1a40-0000-4a00-8000-000000000001"


def _catalog() -> dict:
    """The shared catalog as pack_state resolves it (read-at-call-time)."""
    from tortoise.pack_state import _resolve_catalog
    return _resolve_catalog()


def _expected_defaults() -> list[str]:
    """Starter namespaces expected to activate: the built-in default set
    intersected with what the shared catalog actually carries (a catalog
    change fails loudly instead of silently weakening assertions)."""
    catalog = _catalog()
    assert catalog, "shared pack catalog must resolve in tests"
    return [ns for ns in DEFAULT_STARTER_PACKS if ns in catalog]


def _read_installs(sdk) -> list[tuple]:
    return sdk._get_proj().g.query(
        "MATCH (p:PackInstall) RETURN p.namespace, p.version, p.status, "
        "p.source, p.installed_at ORDER BY p.namespace"
    ).result_set


# ── pack_state: activation semantics ───────────────────────────────────────


class TestEnsureTenantPacks:
    def test_activation_writes_provenance_marked_nodes(self, tmp_path):
        sdk = TortoiseSDK(db_path=str(tmp_path / "a.db"), namespace=f"test_pack_team_a_{os.urandom(4).hex()}")
        activated = ensure_tenant_packs(sdk)
        rows = _read_installs(sdk)
        assert sorted(r[0] for r in rows) == sorted(_expected_defaults())
        assert all(r[1] for r in rows)            # version stamped
        assert all(r[2] == "active" for r in rows)
        assert all(r[3] == "starter" for r in rows)  # provenance mark
        assert all(r[4] for r in rows)            # installed_at stamped
        assert len(activated) == len(_expected_defaults())

    def test_idempotent_rerun_no_duplicates(self, tmp_path):
        sdk = TortoiseSDK(db_path=str(tmp_path / "a.db"), namespace=f"test_pack_team_a_{os.urandom(4).hex()}")
        ensure_tenant_packs(sdk)
        first = _read_installs(sdk)
        ensure_tenant_packs(sdk)   # provision retry / self-heal / backfill
        ensure_tenant_packs(sdk)
        second = _read_installs(sdk)
        assert len(second) == len(first) == len(_expected_defaults())
        # installed_at is preserved across re-runs (coalesce)
        assert sorted(r[0] for r in first) == sorted(r[0] for r in second)

    def test_concurrent_ensures_converge_to_one_node_per_namespace(self, tmp_path):
        """N parallel ensures → exactly ONE PackInstall per namespace
        regardless of interleaving — server-side MERGE atomicity where the
        engine provides it; in-process per-(graph, namespace) serialization
        on the embedded engine (#1307)."""
        sdk = TortoiseSDK(db_path=str(tmp_path / "c.db"), namespace=f"test_pack_team_c_{os.urandom(4).hex()}")
        sdk._get_proj()  # pre-initialize before threading
        n = 8
        barrier = threading.Barrier(n)
        errors: list[Exception] = []

        def _ensure():
            try:
                barrier.wait(timeout=30)
                ensure_tenant_packs(sdk)
            except Exception as e:  # noqa: BLE001, RUF100
                errors.append(e)

        threads = [threading.Thread(target=_ensure) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert not errors, f"concurrent ensures raised: {errors}"
        rows = _read_installs(sdk)
        counts: dict[str, int] = {}
        for r in rows:
            counts[r[0]] = counts.get(r[0], 0) + 1
        assert sorted(counts) == sorted(_expected_defaults())
        assert all(c == 1 for c in counts.values()), (
            f"duplicate PackInstall nodes after concurrent ensures: {counts}")

    def test_unknown_starter_names_skipped_with_warning(self, tmp_path, monkeypatch):
        """Env typo → skipped, never fails provisioning, never ghosts installs."""
        monkeypatch.setenv("TORTOISE_STARTER_PACKS", "dev,bogus-pack,marketing")
        sdk = TortoiseSDK(db_path=str(tmp_path / "a.db"), namespace=f"test_pack_team_a_{os.urandom(4).hex()}")
        activated = ensure_tenant_packs(sdk)  # noqa: F841
        namespaces = sorted(r[0] for r in _read_installs(sdk))
        assert namespaces == ["dev", "marketing"]
        assert "bogus-pack" not in namespaces

    def test_empty_env_falls_back_to_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TORTOISE_STARTER_PACKS", "")
        sdk = TortoiseSDK(db_path=str(tmp_path / "a.db"), namespace=f"test_pack_team_a_{os.urandom(4).hex()}")
        ensure_tenant_packs(sdk)
        assert sorted(r[0] for r in _read_installs(sdk)) == sorted(_expected_defaults())

    def test_additive_only_removal_is_noop(self, tmp_path):
        """Removing a pack from the starter set never uninstalls existing
        installs (non-destructive deactivation-by-config-change)."""
        sdk = TortoiseSDK(db_path=str(tmp_path / "a.db"), namespace=f"test_pack_team_a_{os.urandom(4).hex()}")
        ensure_tenant_packs(sdk, starter=["dev", "marketing"])
        ensure_tenant_packs(sdk, starter=["dev"])   # marketing "removed"
        rows = _read_installs(sdk)
        assert [r[0] for r in rows] == ["dev", "marketing"]
        assert len(rows) == 2  # no uninstall, no dupe

    def test_old_4pack_tenant_converges_to_agent_ops_after_upgrade(self, tmp_path, monkeypatch):
        """R8 (#1933, epic #1891): an EXISTING tenant with the old 4-pack
        starter set (dev/marketing/product-strategy/pm) converges to
        agent-ops active after upgrade with no manual intervention — the
        idempotent additive MERGE of ensure_tenant_packs (the self-heal /
        provisioning read path) adds the new namespace. The CI smoke bound
        derives from len(DEFAULT_STARTER_PACKS), so the starter set is 5."""
        monkeypatch.delenv("TORTOISE_STARTER_PACKS", raising=False)
        assert "agent-ops" in DEFAULT_STARTER_PACKS
        assert len(DEFAULT_STARTER_PACKS) == 5
        sdk = TortoiseSDK(db_path=str(tmp_path / "up.db"), namespace=f"test_pack_team_up_{os.urandom(4).hex()}")
        # pre-upgrade tenant: the OLD 4-pack starter set
        old = ensure_tenant_packs(sdk, starter=["dev", "marketing", "product-strategy", "pm"])
        assert len(old) == 4
        pre_names = sorted(r[0] for r in _read_installs(sdk))
        assert pre_names == ["dev", "marketing", "pm", "product-strategy"]
        assert "agent-ops" not in pre_names
        # post-upgrade: the next ensure_tenant_packs access (self-heal read
        # path) uses the new 5-default — agent-ops MERGEs in additively
        ensure_tenant_packs(sdk)
        rows = _read_installs(sdk)
        names = sorted(r[0] for r in rows)
        assert names == sorted(DEFAULT_STARTER_PACKS)
        assert "agent-ops" in names
        assert len(rows) == len(set(names))  # idempotent — no duplicates
        assert all(r[2] == "active" and r[3] == "starter" for r in rows)
        # the introspection surface agrees (converged, no drift)
        packs = get_tenant_packs(sdk)
        assert "agent-ops" in [p["namespace"] for p in packs]
        assert len(packs) == 5


class TestGetTenantPacks:
    def test_joins_catalog_metadata_sorted(self, tmp_path):
        sdk = TortoiseSDK(db_path=str(tmp_path / "a.db"), namespace=f"test_pack_team_a_{os.urandom(4).hex()}")
        ensure_tenant_packs(sdk)
        packs = get_tenant_packs(sdk)
        catalog = _catalog()
        # Starter set ⊆ catalog — asserting the FULL catalog here (the pre-
        # fix shape) fails the moment the catalog grows a non-starter pack
        # (code-review conf 75, PR #1261). _expected_defaults() keeps this
        # drift-proof.
        assert [p["namespace"] for p in packs] == sorted(_expected_defaults())
        for p in packs:
            assert p["name"] == catalog[p["namespace"]]["name"]
            assert p["version"] == catalog[p["namespace"]]["version"]
            assert p["tier"] == catalog[p["namespace"]]["tier"]
            assert p["status"] == "active" and p["source"] == "starter"

    def test_empty_graph_self_heals_on_first_read(self, tmp_path):
        sdk = TortoiseSDK(db_path=str(tmp_path / "a.db"), namespace=f"test_pack_team_a_{os.urandom(4).hex()}")
        # never activated (e.g. pre-#318 tenant) → first read ensures + returns
        packs = get_tenant_packs(sdk)
        assert sorted(p["namespace"] for p in packs) == sorted(_expected_defaults())
        assert _read_installs(sdk)  # installs now present in the graph

    def test_self_heal_disabled_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PACK_STATE_DISABLE_SELF_HEAL", "1")
        sdk = TortoiseSDK(db_path=str(tmp_path / "a.db"), namespace=f"test_pack_team_a_{os.urandom(4).hex()}")
        assert get_tenant_packs(sdk) == []      # D6: empty, not an error
        assert _read_installs(sdk) == []        # pure eager path untouched

    def test_cross_tenant_isolation(self, tmp_path):
        """Tenant A sees ONLY A's set; tenant B sees ONLY B's — the tenant
        graph is the isolation boundary (no shared mutable state)."""
        db = str(tmp_path / "multi.db")
        # Epic #1647 (T7): distinct per-test test_* namespaces — under URI the
        # redirect's per-path derivation would collapse the literals onto ONE
        # derived graph (same db_path) and the isolation assert would compare
        # a graph to itself (vacuous — #942 class). Distinct test_* names are
        # honored verbatim and stay isolated.
        sdk_a = TortoiseSDK(db_path=db, namespace=f"test_pack_tenant_a_{os.urandom(4).hex()}")
        sdk_b = TortoiseSDK(db_path=db, namespace=f"test_pack_tenant_b_{os.urandom(4).hex()}")
        ensure_tenant_packs(sdk_a, starter=["dev"])
        ensure_tenant_packs(sdk_b, starter=["marketing", "pm"])
        assert [p["namespace"] for p in get_tenant_packs(sdk_a)] == ["dev"]
        assert [p["namespace"] for p in get_tenant_packs(sdk_b)] == ["marketing", "pm"]
        # B's identity on A's surface shape → B's set only (no bleed)
        assert "dev" not in [p["namespace"] for p in get_tenant_packs(sdk_b)]

    def test_partial_failure_converges(self, tmp_path, monkeypatch):
        """One namespace's MERGE fails → next ensure converges without dupes
        (best-effort skip + re-run semantics)."""
        db = str(tmp_path / "p.db")
        sdk = TortoiseSDK(db_path=db, namespace=f"test_pack_team_p_{os.urandom(4).hex()}")

        class _Flaky:
            def __init__(self, g):
                self._g = g
                self._fail_next = True

            def query(self, *a, **kw):
                if self._fail_next:
                    self._fail_next = False
                    raise RuntimeError("simulated MERGE failure")
                return self._g.query(*a, **kw)

        import tortoise.pack_state as ps
        g = sdk._get_proj().g
        monkeypatch.setattr(ps, "_target_graph", lambda sdk_, gn: _Flaky(g))
        ensure_tenant_packs(sdk)      # first namespace fails → skipped
        monkeypatch.setattr(ps, "_target_graph", lambda sdk_, gn: g)
        ensure_tenant_packs(sdk)      # re-run converges
        counts: dict[str, int] = {}
        for r in _read_installs(sdk):
            counts[r[0]] = counts.get(r[0], 0) + 1
        assert sorted(counts) == sorted(_expected_defaults())
        assert all(c == 1 for c in counts.values())


# ── #1307 lock serialization regression guards ────────────────────────────


class _RaceSeam:
    """Replace the atomic MERGE with a deterministic read-then-create.

    Two barriers park racing threads INSIDE the read (both observe
    'absent') and between read and write (both hold their read result) —
    forcing the exact #1307 interleaving (both CREATE) whenever activation
    is NOT serialized. With the per-(graph, ns) lock in place the second
    thread cannot reach the seam until the first finished, so its read
    observes the first thread's write. A lone thread at a barrier (the
    lock held the other back) times out and proceeds alone.
    """

    def __init__(self, g, enter, write_gate):
        self._g = g
        self._enter = enter
        self._write_gate = write_gate

    def query(self, cypher, params=None, timeout=None):
        if "PackInstall" not in cypher or "MERGE" not in cypher:
            return self._g.query(cypher, params=params, timeout=timeout)
        try:  # noqa: SIM105
            self._enter.wait(timeout=1)      # both threads park before the read
        except threading.BrokenBarrierError:
            pass
        existing = self._g.query(
            "MATCH (p:PackInstall {namespace: $ns}) RETURN p.namespace",
            params={"ns": params["ns"]},
        ).result_set
        try:  # noqa: SIM105
            self._write_gate.wait(timeout=1)  # both park before the write
        except threading.BrokenBarrierError:
            pass
        if not existing:
            self._g.query(
                "CREATE (p:PackInstall {namespace: $ns, version: $version, "
                "status: 'active', source: 'starter'})",
                params={"ns": params["ns"], "version": params["version"]},
            )
        return []


def _run_race_pair(sdk, join_timeout=60) -> list[Exception]:
    """Two threads ensure the same SDK concurrently; returns collected errors."""
    errors: list[Exception] = []

    def _ensure():
        try:
            ensure_tenant_packs(sdk, starter=["dev"])
        except Exception as e:  # noqa: BLE001, RUF100
            errors.append(e)

    threads = [threading.Thread(target=_ensure) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=join_timeout)
    return errors


class TestPackInstallLockSerialization:
    def test_same_graph_namespace_share_one_lock(self):
        """conf 95 (PR #1312): lock-keying invariant — same (graph, ns) →
        SAME lock; different namespace or different graph → DIFFERENT lock.
        The serialization guard depends on this invariant; it fails on any
        revert that drops or mis-keys the lock."""
        from tortoise.pack_state import _pack_install_lock
        assert _pack_install_lock("team_a", "dev") is _pack_install_lock("team_a", "dev")
        assert _pack_install_lock("team_a", "dev") is not _pack_install_lock("team_a", "pm")
        assert _pack_install_lock("team_a", "dev") is not _pack_install_lock("team_b", "dev")

    def test_ensure_keys_lock_on_resolved_graph(self, tmp_path, monkeypatch):
        """conf 75 (PR #1312): ensure_tenant_packs keys the lock on the
        RESOLVED graph name, not the passed string. ``graph_name=None``
        (hosted provision) and ``graph_name='team_team-k'`` (backfill read
        target) hit the SAME physical graph and MUST use the same lock.
        Pre-fix keying (``graph_name or 'default'``) produced 'default' vs
        'team_team-k' — a split lock on one graph (and one shared 'default'
        lock across all None callers)."""
        import tortoise.pack_state as ps
        sdk = TortoiseSDK(db_path=str(tmp_path / "k.db"), namespace="team-k")
        seen: list[str] = []
        real = ps._pack_install_lock

        def _spy(graph, ns):
            seen.append(graph)
            return real(graph, ns)

        monkeypatch.setattr(ps, "_pack_install_lock", _spy)
        ensure_tenant_packs(sdk, graph_name=None, starter=["dev"])
        ensure_tenant_packs(sdk, graph_name="team_team-k", starter=["dev"])
        assert seen, "lock must be consulted for every activation"
        assert seen[0] == seen[1], (
            f"lock keyed on different graphs: {seen}")
        if os.environ.get("TORTOISE_DB_URI"):
            # Epic #1647 (PR #1684 CI-fix): on the docker lane the redirect
            # derives per-path test_<stem>_<hash> names — both calls must
            # lock the SAME derived physical graph (the conf 75 property),
            # not the raw team_team-k literal (which doesn't exist on the
            # server).
            assert seen[0].startswith("test_"), (
                f"docker lane must lock the derived graph, got {seen[0]}")
        else:
            assert seen == ["team_team-k", "team_team-k"], (
                f"lock keyed on passed string, not resolved graph: {seen}")

    def test_race_seam_reproduces_duplicates_without_serialization(
            self, tmp_path, monkeypatch):
        """Red demonstration (conf 95): with the lock neutralized (unfixed
        behavior — no-op lock), the deterministic read-then-create seam
        produces EXACTLY the #1307 duplicate. Proves the seam is a faithful
        repro; without it, the green test below would pass vacuously even if
        the seam forced no interleaving at all."""
        from contextlib import nullcontext  # noqa: I001
        import tortoise.pack_state as ps
        if getattr(ps, "_pack_install_lock", None) is not None:
            monkeypatch.setattr(ps, "_pack_install_lock",
                                lambda graph, ns: nullcontext())
        db = str(tmp_path / "red.db")
        sdk = TortoiseSDK(db_path=db, namespace=f"test_pack_team_red_{os.urandom(4).hex()}")
        sdk._get_proj()  # pre-initialize before threading
        g = sdk._get_proj().g
        seam = _RaceSeam(g, threading.Barrier(2), threading.Barrier(2))
        monkeypatch.setattr(ps, "_target_graph", lambda sdk_, gn: seam)
        errors = _run_race_pair(sdk)
        assert not errors, f"ensures raised: {errors}"
        dev_count = sum(1 for r in _read_installs(sdk) if r[0] == "dev")
        assert dev_count == 2, (
            f"race seam did not reproduce the #1307 duplicate (got "
            f"{dev_count}); the seam no longer forces the interleaving")

    def test_concurrent_ensures_with_race_seam_converge(
            self, tmp_path, monkeypatch):
        """Green regression guard (conf 95, PR #1312): the per-(graph, ns)
        lock serializes activation, so even under the race-forcing seam
        exactly ONE node survives. Fails on unfixed code — the missing lock
        lets both threads rendezvous in the seam's read and both CREATE
        (duplicate, as #1307 observed). Deterministic: no timing luck."""
        import tortoise.pack_state as ps
        db = str(tmp_path / "green.db")
        sdk = TortoiseSDK(db_path=db, namespace=f"test_pack_team_green_{os.urandom(4).hex()}")
        sdk._get_proj()  # pre-initialize before threading
        g = sdk._get_proj().g
        seam = _RaceSeam(g, threading.Barrier(2), threading.Barrier(2))
        monkeypatch.setattr(ps, "_target_graph", lambda sdk_, gn: seam)
        errors = _run_race_pair(sdk)
        assert not errors, f"ensures raised: {errors}"
        dev_count = sum(1 for r in _read_installs(sdk) if r[0] == "dev")
        assert dev_count == 1, (
            f"duplicate PackInstall after serialized activation: {dev_count}")


# ── Provisioning hooks ─────────────────────────────────────────────────────


@pytest.fixture
def registry_client(monkeypatch):
    """TestClient in REGISTRY control-plane mode (selfhost) with a temp
    embedded DB — for /internal/provision (disabled under Supabase mode)."""
    from fastapi.testclient import TestClient  # noqa: I001
    from tortoise.hosted_api import app
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "reg.db")
        # #2127 wave 2: shared helper — patch __init__ → temp DB, #1950
        # TORTOISE_DB_PATH pin, close-then-clear at enter; pop-env → restore
        # __init__ → deterministic anchor close → clear overrides at exit
        # (the old restore was restore-init-only — no pin, no anchor close:
        # the #1950 clear-without-close leak shape this wave fixes).
        with patched_tortoise_sdk(db_path), TestClient(app) as tc:
            yield tc, db_path


@pytest.fixture
def supabase_client(monkeypatch):
    """TestClient in SUPABASE control-plane mode with a FakeControlPlane
    (zero network) + a temp embedded DB — mirrors test_writer_inventory."""
    from fastapi.testclient import TestClient  # noqa: I001
    from tortoise.hosted_api import app, get_current_team, get_current_user  # noqa: F401
    from tests.fake_control_plane import FakeControlPlane
    from tests.test_supabase_control import FREE_TEAM

    fake = FakeControlPlane({
        "api_keys": [],
        "team_memberships": [],
        "teams": [dict(FREE_TEAM)],
        "invitations": [],
    })
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_role_key_test")
    import tortoise.supabase_control as sc
    monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "inv.db")
        # #2127 wave 2: shared helper (see registry_client — same pin +
        # deterministic-close upgrade over the old restore-only shape).
        with patched_tortoise_sdk(db_path), TestClient(app) as tc:
            yield tc, fake, db_path


_INTERNAL_HEADERS = {"Authorization": "Bearer test-internal-shared-secret-xyz"}


class TestProvisioningHooks:
    def test_internal_provision_activates_packs(self, registry_client):
        """Site 1 (registry mode): provision → PackInstall nodes present in
        the tenant graph immediately (pre-GET — pure eager path)."""
        tc, db_path = registry_client
        r = tc.post("/internal/provision", headers=_INTERNAL_HEADERS, json={
            "team_id": "t-reg-1", "team_name": "Reg One",
            "api_key_hash": "abc", "created_by": "user-1",
        })
        assert r.status_code == 200, r.text
        team_id = r.json()["team_id"]
        sdk = TortoiseSDK(db_path=db_path, namespace=team_id)
        rows = _read_installs(sdk)
        assert sorted(r[0] for r in rows) == sorted(_expected_defaults())

    def test_internal_provision_idempotent_rerun(self, registry_client):
        tc, db_path = registry_client
        body = {"team_id": "t-reg-2", "team_name": "Reg Two",
                "api_key_hash": "abc", "created_by": "user-1"}
        assert tc.post("/internal/provision", headers=_INTERNAL_HEADERS,
                       json=body).status_code == 200
        # re-provisioning with the same team_id re-activates via the
        # idempotent path (graph deletion wipes installs; re-provision heals)
        r2 = tc.post("/internal/provision", headers=_INTERNAL_HEADERS, json=body)
        assert r2.status_code == 200
        sdk = TortoiseSDK(db_path=db_path, namespace="t-reg-2")
        rows = _read_installs(sdk)
        assert len(rows) == len(_expected_defaults())

    def test_register_activates_packs_supabase_mode(self, supabase_client):
        """Site 2+3 (Supabase mode /v1/register): the provision_team RPC hook
        activates the starter set into the tenant graph."""
        tc, fake, db_path = supabase_client
        r = tc.post("/v1/register", json={
            "email": "founder@example.com", "password": "hunter2secret"})
        assert r.status_code == 200, r.text
        team_id = r.json()["team_id"]
        assert fake.rpc_calls[0][0] == "provision_team"
        sdk = TortoiseSDK(db_path=db_path, namespace=team_id)
        rows = _read_installs(sdk)
        assert sorted(r[0] for r in rows) == sorted(_expected_defaults())

    def test_create_team_activates_packs_supabase_mode(self, supabase_client):
        """Site 2 (/v1/teams): activation rides the provision_team RPC hook."""
        from tortoise.hosted_api import app, get_current_user
        tc, fake, db_path = supabase_client
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _U1, "email": "user-1@example.com"}
        r = tc.post("/v1/teams", json={"name": "acme"})
        assert r.status_code == 200, r.text
        team_id = r.json()["team_id"]
        assert fake.rpc_calls[0][0] == "provision_team"
        sdk = TortoiseSDK(db_path=db_path, namespace=team_id)
        rows = _read_installs(sdk)
        assert sorted(r[0] for r in rows) == sorted(_expected_defaults())


# ── REST introspection: GET /v1/packs ──────────────────────────────────────


class TestGetV1Packs:
    def test_requires_auth(self, supabase_client):
        from tortoise.hosted_api import app, get_current_team  # noqa: F401
        tc, _, _ = supabase_client
        r = tc.get("/v1/packs")
        # get_current_team 401s first (no Authorization header)
        assert r.status_code == 401

    def test_returns_tenant_packs_with_auth(self, supabase_client):
        from tortoise.hosted_api import app, get_current_team
        tc, _, _ = supabase_client
        app.dependency_overrides[get_current_team] = lambda: {
            "team_id": "team-free-001", "key_id": "k1", "tier": "free"}
        r = tc.get("/v1/packs")
        assert r.status_code == 200, r.text
        packs = r.json()["packs"]
        # get_current_team override team has no graph data yet → self-heal
        assert sorted(p["namespace"] for p in packs) == sorted(_expected_defaults())

    def test_empty_masking_when_nothing_to_see(self, supabase_client, monkeypatch):
        """D6: same-tenant no-installs (self-heal disabled + empty starter
        set) → empty list, 200 — never an error."""
        from tortoise.hosted_api import app, get_current_team
        monkeypatch.setenv("PACK_STATE_DISABLE_SELF_HEAL", "1")
        monkeypatch.setenv("TORTOISE_STARTER_PACKS", "")
        tc, _, _ = supabase_client
        app.dependency_overrides[get_current_team] = lambda: {
            "team_id": "team-free-001", "key_id": "k1", "tier": "free"}
        r = tc.get("/v1/packs")
        assert r.status_code == 200, r.text
        assert r.json() == {"packs": []}

    def test_team_id_none_fails_closed(self, supabase_client):
        """team_id None (SKIP_AUTH/background shape) → 401, never a
        default-namespace fallback."""
        from tortoise.hosted_api import app, get_current_team
        tc, _, _ = supabase_client
        app.dependency_overrides[get_current_team] = lambda: {
            "team_id": None, "tier": "free", "key_id": None}
        r = tc.get("/v1/packs")
        assert r.status_code == 401

    def test_two_tokens_no_bleed(self, supabase_client, monkeypatch):
        """AC2 (isolation): tenant A's surface returns A's set; a second
        team (different namespace) returns its own set — no bleed."""
        from tortoise.hosted_api import app, get_current_team
        tc, _, db_path = supabase_client
        # Seed two distinct tenant graphs with distinct starter sets.
        sdk_a = TortoiseSDK(db_path=db_path, namespace="tenant-a")
        sdk_b = TortoiseSDK(db_path=db_path, namespace="tenant-b")
        ensure_tenant_packs(sdk_a, starter=["dev"])
        ensure_tenant_packs(sdk_b, starter=["marketing"])

        app.dependency_overrides[get_current_team] = lambda: {
            "team_id": "tenant-a", "key_id": "k-a", "tier": "free"}
        r_a = tc.get("/v1/packs")
        assert r_a.status_code == 200
        assert [p["namespace"] for p in r_a.json()["packs"]] == ["dev"]

        app.dependency_overrides[get_current_team] = lambda: {
            "team_id": "tenant-b", "key_id": "k-b", "tier": "free"}
        r_b = tc.get("/v1/packs")
        assert r_b.status_code == 200
        assert [p["namespace"] for p in r_b.json()["packs"]] == ["marketing"]


# ── MCP introspection: packs_list ──────────────────────────────────────────


def _mcp_parse(r):
    text = r.text
    if text.startswith("event:") or "\ndata: " in text:
        for line in text.splitlines():
            if line.startswith("data: "):
                import json
                return json.loads(line[len("data: "):])
        return None
    return r.json()


class TestMcpPacksList:
    def test_handler_scoped_to_team(self, tmp_path, monkeypatch):
        """Unit-level: the handler reads the team SDK via the contextvar
        seam and returns the team's packs (threadpool-safe pattern)."""
        from tortoise import mcp_auth  # noqa: I001
        import tortoise.mcp_server as ms
        db = str(tmp_path / "mcp.db")
        sdk = TortoiseSDK(db_path=db, namespace="mcp-team")
        ensure_tenant_packs(sdk, starter=["dev", "pm"])
        monkeypatch.setattr(ms, "_get_team_sdk",
                            lambda: TortoiseSDK(db_path=db,
                                                namespace=mcp_auth._current_team_id.get()))
        t_mode = mcp_auth._transport_mode.set("stdio")
        token = mcp_auth._current_team_id.set("mcp-team")
        try:
            packs = ms.tortoise_packs_list()
        finally:
            mcp_auth._current_team_id.reset(token)
            mcp_auth._transport_mode.reset(t_mode)
        assert [p["namespace"] for p in packs] == ["dev", "pm"]

    def test_handler_fails_closed_on_none_team_http(self, monkeypatch):
        from tortoise import mcp_auth  # noqa: I001
        import tortoise.mcp_server as ms
        token = mcp_auth._transport_mode.set("http")
        t2 = mcp_auth._current_team_id.set(None)
        try:
            out = ms.tortoise_packs_list()
        finally:
            mcp_auth._current_team_id.reset(t2)
            mcp_auth._transport_mode.reset(token)
        assert out.get("error") and "team context" in out["error"].lower()

    def test_http_surface_registered_and_scoped(self, tmp_path, monkeypatch):
        """AC3: packs_list over the HTTP MCP surface returns the tenant-
        scoped set (http_policy=True → in HTTP_ALLOWED)."""
        from tortoise import mcp_auth  # noqa: I001
        import tortoise.mcp_server as ms
        from tortoise.mcp_server import create_http_app
        from starlette.applications import Starlette
        from starlette.routing import Mount
        from starlette.testclient import TestClient

        # registry SDK (auth resolution) + team SDK share one embedded DB
        db = str(tmp_path / "mcp-http.db")
        reg_sdk = TortoiseSDK(db_path=db, namespace=f"test_pack_registry_{os.urandom(4).hex()}")
        team = reg_sdk.team_create("test-team")
        key_info = reg_sdk.apikey_create(team["id"], "test-fixture")
        sdk_team = TortoiseSDK(db_path=db, namespace=team["id"])
        ensure_tenant_packs(sdk_team, starter=["dev", "marketing"])

        # the tool handler resolves _get_team_sdk from mcp_server's module
        # namespace — patch that name (not mcp_auth's) so the team SDK
        # targets the shared embedded DB.
        monkeypatch.setattr(
            ms, "_get_team_sdk",
            lambda: TortoiseSDK(db_path=db,
                                namespace=mcp_auth._current_team_id.get()))

        app = create_http_app(allowed_origins=["https://app.premiselabs.co"],
                              _registry_sdk=reg_sdk)

        @asynccontextmanager
        async def _lifespan(parent_app):
            async with app.lifespan(app):
                yield

        parent = Starlette(lifespan=_lifespan,
                           routes=[Mount("/mcp", app=app)])
        with TestClient(parent) as tc:
            tc.headers.update({
                "Authorization": f"Bearer {key_info['api_key']}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            })
            r = tc.post("/mcp", json={
                "jsonrpc": "2.0", "method": "tools/call",
                "params": {"name": "tortoise_packs_list", "arguments": {}},
                "id": 1})
            assert r.status_code == 200, r.text
            body = _mcp_parse(r)
            assert body and body.get("result"), body
            content = body["result"]["content"]
            assert content and content[0].get("text")
            import json as _json
            packs = _json.loads(content[0]["text"])
            assert [p["namespace"] for p in packs] == ["dev", "marketing"]

    def test_http_allowed_contains_packs_list(self):
        from tortoise.mcp_auth import HTTP_ALLOWED
        assert "tortoise_packs_list" in HTTP_ALLOWED


# ── Backfill script ────────────────────────────────────────────────────────


class TestBackfillScript:
    @pytest.mark.embedded_only  # epic #1647 D-2=A: real FalkorDB has no busy concept — this stays embedded
    def test_dry_run_default_makes_no_writes(self, tmp_path, monkeypatch):
        """D5: --dry-run is the default; a run reports without writing.

        Runs the script IN-PROCESS (embedded FalkorDBLite is single-writer —
        a subprocess would hit EmbeddedStoreBusyError on the same DB).
        """
        # Import the script from its path (graph-scripts is not a package).
        import importlib.machinery
        import importlib.util

        from fastapi.testclient import TestClient

        from tortoise.hosted_api import app
        _script = os.path.join(REPO_ROOT, "graph-scripts",
                               "backfill_pack_installs.py")
        _loader = importlib.machinery.SourceFileLoader(
            "bf_script", _script)
        _spec = importlib.util.spec_from_loader("bf_script", _loader)
        bf = importlib.util.module_from_spec(_spec)
        _loader.exec_module(bf)

        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
        # Hermetic selfhost shape: backfill's bare TortoiseSDK() resolves via
        # TORTOISE_DB_PATH — a runner-set TORTOISE_DB_URI (unsupported
        # embedded:// scheme) would route through from_uri and abort.
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "bf.db")
            monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
            # #2127 wave 2: shared helper (see registry_client fixture).
            # Scoped to the provision TestClient ONLY: the dry-run's bare
            # tortoise.sdk constructions run AFTER init restore and resolve
            # via the monkeypatch-set TORTOISE_DB_PATH pin — the helper's
            # exit snapshot-RESTORES the pin (to the monkeypatch value, still
            # db_path), so it stays set for bf.main() and the post-run reads.
            with patched_tortoise_sdk(db_path), TestClient(app) as tc:
                # seed a team via the registry provision path (eager
                # activation writes the starter set)
                r = tc.post("/internal/provision",
                            headers=_INTERNAL_HEADERS, json={
                                "team_id": "t-bf-1", "team_name": "BF",
                                "api_key_hash": "abc",
                                "created_by": "user-1"})
                assert r.status_code == 200, r.text

            # dry-run: exit 0, prints per-team plans, writes nothing
            monkeypatch.setattr(sys, "argv", ["backfill_pack_installs.py",
                                              "--limit", "5"])
            rc = bf.main()
            assert rc == 0
            sdk = TortoiseSDK(db_path=db_path, namespace="t-bf-1")
            rows = _read_installs(sdk)
            # dry-run must not add/duplicate anything: only the EAGER installs
            # from provisioning are present (a duplicate would inflate len)
            assert len(rows) == len(_expected_defaults())

    def test_apply_writes_to_introspection_read_target(self, tmp_path,
                                                      monkeypatch):
        """conf 70 (PR #1261): --apply lands installs in the READ TARGET
        (team_{team_id}) even for a legacy team whose recorded graph_name is
        team_{name} — the read surface (get_tenant_packs) must see the
        backfilled records, and the legacy graph must stay untouched."""
        import importlib.machinery
        import importlib.util
        _script = os.path.join(REPO_ROOT, "graph-scripts",
                               "backfill_pack_installs.py")
        _loader = importlib.machinery.SourceFileLoader("bf_script", _script)
        _spec = importlib.util.spec_from_loader("bf_script", _loader)
        bf = importlib.util.module_from_spec(_spec)
        _loader.exec_module(bf)

        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
        # Embedded selfhost shape: TORTOISE_DB_PATH set, NO TORTOISE_DB_URI —
        # the backfill's bare TortoiseSDK(namespace=...) calls resolve to the
        # seeded DB via resolve_db_path() (the conf-70 scenario).
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "bf.db")
            monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
            # Seed a LEGACY pre-#318 team via sdk.team_create: records
            # graph_name team_{name} on the Team node and activates NO packs
            # (no eager install-state — exactly a legacy tenant). Namespace=None
            # SDK: registry graph is `control_plane`, matching the backfill's
            # _iter_teams enumeration (TortoiseSDK() → control_plane) — a
            # namespace="registry" SDK would write to registry_control_plane
            # and the backfill would never see the team.
            created = TortoiseSDK(db_path=db_path).team_create("LegacyCo")
            team_id, legacy_graph = created["id"], created["graph_name"]
            assert legacy_graph == "team_LegacyCo"
            sdk_legacy = TortoiseSDK(db_path=db_path, namespace=team_id)
            assert _read_installs(sdk_legacy) == []  # legacy: no installs

            # --apply: backfill the legacy team's starter set
            monkeypatch.setattr(sys, "argv",
                                ["backfill_pack_installs.py", "--apply"])
            assert bf.main() == 0

            # Read surface sees the backfilled records WITHOUT self-heal (if
            # --apply had landed them in team_LegacyCo, this read would return
            # [] — the conf-70 bug; self-heal would mask it, so it's disabled).
            monkeypatch.setenv("PACK_STATE_DISABLE_SELF_HEAL", "1")
            packs = get_tenant_packs(TortoiseSDK(db_path=db_path,
                                                 namespace=team_id))
            assert sorted(p["namespace"] for p in packs) == \
                sorted(_expected_defaults())
            # installs landed in the READ TARGET team_{team_id} graph...
            assert sorted(r[0] for r in _read_installs(sdk_legacy)) == \
                sorted(_expected_defaults())
            # ...and the legacy team_{name} graph stays untouched (no invisible
            # duplicate set the self-heal would otherwise mint).
            legacy_rows = sdk_legacy._get_proj().db.select_graph(
                legacy_graph).query(
                "MATCH (p:PackInstall) RETURN p.namespace").result_set
            assert legacy_rows == []
