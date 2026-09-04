"""#2031 — tenant-view consumer wiring: hosted extraction mints tenant pack kinds.

Extends the #1935 tenant-pack surface (test-design #1898 surface 8 / E2E-6)
with the CONSUMER path the #1935 PR left unwired: the hosted extraction
vocabulary compile must include the tenant's manifests (their kinds +
memory_granularity + kindDefs) so tenant A's hosted extraction mints A's
pack kinds, tenant B never sees them, and the tenant-view memoization is
exercised through the consumer path (cache hit on repeat, invalidation on
write — including the sha256-in-key cross-process signal).

Covers:
- tenant-scoped master (build_master_list(sdk)) includes tenant
  kinds (declared + kindDef'd) and never touches the global _MASTER_LIST_CACHE
- hosted capture for tenant A with a custom pack mints A's kind (positive)
- tenant B's extraction never sees A's kinds (negative, both prompts+graph)
- tenant-view memo hit (identity) + write invalidation (new kinds reach the
  master) + sha256-key invalidation (same ns+version, different content)
- tenant memory_granularity reaches the S1 prompt (via _granularity_text)
- default path byte-identity (compile_value_brief() / build_master_list()
  unchanged; compact-mode pack selection keeps tenant kinds)

Docker lane (default): TORTOISE_DB_URI must be set (epic #1647 P4).
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from typing import ClassVar

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")
os.environ.setdefault("FASTAPI_INTERNAL_KEY", "test-internal-shared-secret-xyz")
# NOTE (#2031 review): TORTOISE_SESSION_LLM_MOCK is deliberately NOT set at
# import time — a module-level setdefault would leak the mock seam into the
# whole pytest session (test_hosted_api/test_capture_session share the
# process). It is set per-test via the mock_extractor fixture.

import pytest
from fastapi.testclient import TestClient

import tortoise.hosted_api as ha_mod
import tortoise.sdk as sdk_mod
from tests._http_fixtures import patched_tortoise_sdk
from tortoise.hosted_api import app, get_current_team

TEST_TEAM_ID = f"team-{uuid.uuid4().hex[:8]}"
TEST_TEAM = {
    "team_id": TEST_TEAM_ID,
    "key_id": "test-key-001",
    # C5 #2114 (#2260): legacy tt_ class — scope-less key_id dicts 403 the
    # pack-upload scope gate otherwise; mirrors test_pack_manifest_store.py:45.
    "legacy_full_access": True,
    "tier": "free",
    "max_users": 1, "max_graphs": 1, "max_points": 10000,
    "max_api_keys": 2, "max_sessions": 1000,
}
TEST_TEAM_B = {"team_id": f"team-{uuid.uuid4().hex[:8]}", "key_id": "test-key-002",
               "legacy_full_access": True,
               "tier": "free", "max_users": 1, "max_graphs": 1,
               "max_points": 10000, "max_api_keys": 2, "max_sessions": 1000}

# The #1935 fixture shape: a declared object kind WITHOUT a kindDef (the
# tenant-kind parity surface — FIX M declared-kind acceptance).
VALID_MANIFEST = """namespace: tenant-ops
name: Tenant Operations
version: 0.1.0
tier: free
ontology:
  extends: core
  objectKinds:
  - contract
"""

# Same namespace, bumped version + kindDef'd kind + memory_granularity (the
# write-invalidation + S1-granularity probe). kindDefs kinds must be
# declared in the *Kinds lists (shared validator).
VALID_MANIFEST_V2 = (
    VALID_MANIFEST
    .replace("  - contract\n", "  - contract\n  - sla\n")
    .replace("version: 0.1.0", "version: 0.2.0")
    + """  kindDefs:
    sla:
      description: A service-level agreement commitment
      nearMisses:
      - contract
  memory_granularity: 'Durable: contract terms. Ephemeral: negotiation chatter.'
"""
)


class _TenantAwareMock:
    """Master-aware v2 extractor stand-in (#2031): emits the tenant kind
    ONLY when the prompt actually carries it — a real positive-minting probe
    (the shipped _V2SessionMock returns a fixed core:strategy and never reads
    the prompts, which would make a positive test vacuous). Records the S1
    (STORY SUMMARIZER) and S2/S4 prompt system strings for content asserts."""

    s1_prompts: ClassVar[list[str]] = []
    s2_prompts: ClassVar[list[str]] = []

    def complete(self, *, system: str, user: str,
                 max_tokens: int | None = None) -> str:
        if "STORY SUMMARIZER" in system:
            _TenantAwareMock.s1_prompts.append(system)
            return "The session produced a contract for the tenant."
        _TenantAwareMock.s2_prompts.append(system)
        kind = ("tenant-ops:contract"
                if "tenant-ops:contract" in system else "core:strategy")
        return json.dumps({
            "entities": [{"name": "the contract", "kind": kind,
                          "lifecycle": "created", "supersedes": None,
                          "note": None}],
            "events": [{"content": "we decided on the contract",
                        "eventKind": "core:decision",
                        "about_entities": ["the contract"]}],
            "points": [{"content": "the contract is durable",
                        "pointKind": "statement",
                        "about_entities": ["the contract"]}],
            "operators": [], "chain_notes": [], "link_before_create": [],
        })


@pytest.fixture
def mock_extractor(monkeypatch):
    """Route the v2 mock seam to the prompt-reading _TenantAwareMock.
    Sets TORTOISE_SESSION_LLM_MOCK=1 per-test (the hosted capture's provider
    gate fails closed without it) and resets the recorded prompts."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    _TenantAwareMock.s1_prompts = []
    _TenantAwareMock.s2_prompts = []
    monkeypatch.setattr(sdk_mod, "_V2SessionMock", _TenantAwareMock)
    return _TenantAwareMock


@pytest.fixture
def client():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        app.dependency_overrides[get_current_team] = lambda: dict(TEST_TEAM)
        # #2127: shared helper (tests._http_fixtures.patched_tortoise_sdk).
        # sdk_mod.TortoiseSDK is hosted_api.TortoiseSDK (same class object) —
        # the helper's hosted_api patch applies identically; it adds the
        # #1950 TORTOISE_DB_PATH pin + deterministic close of the
        # _team_sdk/_make_sdk anchors at exit (this file's local helper
        # neither pinned nor closed — the churn class).
        with patched_tortoise_sdk(db_path):
            yield TestClient(app)


def _team_sdk(team_id: str = TEST_TEAM_ID):
    return ha_mod._make_sdk(namespace=team_id)


def _count_object_kind_nodes(sdk, object_kind: str) -> int:
    """Bounded-retry graph probe for minted :Object nodes of a kind
    (tolerates the embedded lane's write-visibility lag — the
    test_hosted_api _listed_key pattern; these tests patch the SDK to a temp
    sqlite graph). v2 entities materialize as :Object {objectKind}
    (create_entity type='object')."""
    proj = sdk._get_proj()
    deadline = time.monotonic() + 3.0
    while True:
        rows = proj.g.query(
            "MATCH (n:Object {objectKind: $kind}) RETURN count(n)",
            params={"kind": object_kind},
        ).result_set
        n = rows[0][0] if rows else 0
        if n > 0 or time.monotonic() >= deadline:
            return n
        time.sleep(0.15)


def _upload(client_, manifest_yaml: str) -> int:
    return client_.post("/v1/packs/manifests",
                        json={"manifest_yaml": manifest_yaml}).status_code


# ── Tenant-scoped master (#2031 consumer path) ─────────────────────────────

class TestTenantMaster:
    def test_tenant_master_includes_tenant_kinds(self, client):
        """build_master_list(sdk) compiles the tenant's pack kinds
        (declared + kindDef'd) into pack_kinds; the default path never sees
        them and the global _MASTER_LIST_CACHE stays untouched (#1154)."""
        from tortoise.extractor_v2 import build_master_list
        assert _upload(client, VALID_MANIFEST) == 201
        sdk = _team_sdk()
        master = build_master_list(sdk=sdk)
        assert "tenant-ops:contract" in master["pack_kinds"], \
            "tenant declared kind must reach the tenant master"
        # Default path: no tenant kinds, and unchanged by the tenant call.
        default_before = build_master_list()
        assert "tenant-ops:contract" not in default_before["pack_kinds"]
        build_master_list(sdk=sdk)  # repeat tenant call
        assert build_master_list() == default_before, \
            "tenant-scoped compile must not poison _MASTER_LIST_CACHE"

    def test_default_master_pack_kinds_byte_identity(self):
        """The default master's pack_kinds keyset + order match the shared
        catalog exactly (the PackRegistry over default_packs_dir() is the
        independent oracle) — the PACK_NS parameterization cannot drift."""
        from tortoise.extractor_v2 import build_master_list
        from tortoise.pack_registry import PackRegistry, default_packs_dir
        from tortoise.value_extractor import compile_value_brief
        master = build_master_list()
        brief = compile_value_brief()
        reg = PackRegistry(default_packs_dir())
        reg.load_all()
        expected = {k: master["pack_kinds"].get(k, "")
                    for k in brief
                    if k != "memory_granularity" and ":" in k
                    and k.rsplit(":", 1)[0] in reg.packs}
        assert list(master["pack_kinds"]) == list(expected), \
            "default pack_kinds keyset/order drifted"
        assert "tenant-ops" not in " ".join(master["pack_kinds"])

    def test_compact_selection_keeps_tenant_kinds(self, client):
        """TORTOISE_EXTRACTOR_PROMPT=compact: a namespace with no trigger
        entry (tenant packs) is always included — a partial starter-trigger
        match must not strip the tenant's own kinds (verifier P3)."""
        from tortoise.extractor_v2 import _select_pack_kinds, build_master_list
        assert _upload(client, VALID_MANIFEST) == 201
        master = build_master_list(sdk=_team_sdk())
        # A story matching ONLY a starter trigger (dev words, no tenant words).
        selected = _select_pack_kinds(
            "the epic issue was deployed after the code review",
            master["pack_kinds"])
        assert "tenant-ops:contract" in selected, \
            "tenant kinds must survive compact-mode story selection"


# ── Hosted extraction minting (positive) ───────────────────────────────────

class TestHostedExtractionMinting:
    def test_tenant_a_extraction_mints_tenant_kind(
            self, client, mock_extractor):
        """E2E-6 positive: tenant A uploads a custom pack, then a hosted
        capture for A mints A's pack kind as a real graph kind — the mock
        emits it only because A's S2 prompt carries it (prompt-inspection
        probe), and the S5 gate writes it un-repaired."""
        assert _upload(client, VALID_MANIFEST) == 201
        conv = [
            {"role": "user", "content": "Let's sign the service contract."},
            {"role": "assistant", "content": "Agreed — the contract is set."},
        ]
        r = client.post("/v1/sessions", json={"conversation": conv})
        assert r.status_code == 200, r.text
        body = r.json()
        assert any("tenant-ops:contract" in p
                   for p in mock_extractor.s2_prompts), \
            "A's extraction prompt must carry A's pack kind"
        # No minted-repair warning for the tenant kind (it is writable).
        assert not any("minted entity kind 'tenant-ops:contract'" in w
                       for w in body.get("warnings", [])), \
            f"tenant kind flagged minted: {body.get('warnings')}"
        assert _count_object_kind_nodes(_team_sdk(), "tenant-ops:contract") >= 1, \
            "tenant A's pack kind must be minted as a real graph kind"

    def test_tenant_a_memory_granularity_reaches_s1(
            self, client, mock_extractor):
        """The tenant manifest's memory_granularity reaches the S1 prompt
        (the _granularity_text master threading — verifier P2)."""
        assert _upload(client, VALID_MANIFEST_V2) == 201
        r = client.post("/v1/sessions", json={"conversation": [
            {"role": "user", "content": "The contract terms are set."}]})
        assert r.status_code == 200, r.text
        assert any("tenant-ops" in p for p in mock_extractor.s1_prompts), \
            "tenant memory_granularity must reach the S1 prompt"


# ── Cross-tenant isolation (negative) ──────────────────────────────────────

class TestCrossTenantNegative:
    def test_tenant_b_never_sees_tenant_a_kinds(self, mock_extractor):
        """E2E-6 negative: tenant B (no custom pack) captures the SAME
        conversation and never sees A's kinds — neither in B's prompts nor
        as minted nodes (structural graph-namespace isolation + tenant-scoped
        compile)."""
        with tempfile.TemporaryDirectory() as tmp_a, \
                tempfile.TemporaryDirectory() as tmp_b:
            db_a = os.path.join(tmp_a, "a.db")
            db_b = os.path.join(tmp_b, "b.db")

            def _route_patch(self, db_path_arg=None, *, namespace=None,
                             **kwargs):
                target = db_b if namespace == TEST_TEAM_B["team_id"] else db_a
                _orig(self, target, namespace=namespace)

            _orig = sdk_mod.TortoiseSDK.__init__
            sdk_mod.TortoiseSDK.__init__ = _route_patch
            app.dependency_overrides[get_current_team] = lambda: dict(TEST_TEAM)
            try:
                c_a = TestClient(app)
                assert _upload(c_a, VALID_MANIFEST) == 201
                conv = [
                    {"role": "user", "content": "Let's sign the service contract."},
                    {"role": "assistant", "content": "Agreed — the contract is set."},
                ]
                # Tenant A captures (mints the tenant kind).
                r_a = c_a.post("/v1/sessions", json={"conversation": conv})
                assert r_a.status_code == 200, r_a.text
                assert _count_object_kind_nodes(
                    ha_mod._make_sdk(namespace=TEST_TEAM_ID),
                    "tenant-ops:contract") >= 1

                # Tenant B captures the same conversation.
                mock_extractor.s2_prompts = []
                app.dependency_overrides[get_current_team] = \
                    lambda: dict(TEST_TEAM_B)
                c_b = TestClient(app)
                r_b = c_b.post("/v1/sessions", json={"conversation": conv})
                assert r_b.status_code == 200, r_b.text
                # B's extraction must have run (the mock path works — proven
                # by A's minting asserts above) before the negative checks.
                assert mock_extractor.s2_prompts, \
                    "tenant B's extraction produced no prompts — vacuous negative"
                # B's prompts never carry A's kind (prompt-inspection probe).
                assert not any("tenant-ops:contract" in p
                               for p in mock_extractor.s2_prompts), \
                    "tenant B's extraction prompt must never carry A's kinds"
                # B's graph has no tenant-ops node.
                assert _count_object_kind_nodes(
                    ha_mod._make_sdk(namespace=TEST_TEAM_B["team_id"]),
                    "tenant-ops:contract") == 0, \
                    "tenant B's graph must never mint tenant A's kinds"
                # B's master lacks A's kinds.
                from tortoise.extractor_v2 import build_master_list
                b_master = build_master_list(sdk=ha_mod._make_sdk(namespace=TEST_TEAM_B["team_id"]))
                assert "tenant-ops:contract" not in b_master["pack_kinds"], \
                    "tenant B's master must not contain tenant A's kinds"
            finally:
                app.dependency_overrides.clear()
                sdk_mod.TortoiseSDK.__init__ = _orig


# ── Tenant-view memoization through the consumer path ──────────────────────

class TestMemoization:
    def test_graph_identity_is_per_team(self, client):
        """#2031 review fix: ``_graph_identity`` resolves the SDK's
        namespace-scoped graph name (the pre-fix one-arg call TypeError'd
        into the catch-all and collapsed EVERY tenant's memo key to
        'default' — one shared memo entry + one global dirty flag, activated
        once tenant_view gained its hosted-consumer)."""
        import tortoise.pack_manifest_store as pms
        assert pms._graph_identity(_team_sdk()) != "default"
        assert (pms._graph_identity(_team_sdk())
                != pms._graph_identity(
                    ha_mod._make_sdk(namespace=TEST_TEAM_B["team_id"]))), \
            "each tenant must own a distinct memo key"

    def test_fleet_cap_eviction_does_not_crash(self, client, monkeypatch):
        """Second-model gate fix: the fleet-cap eviction extracts the gid
        from the KEY tuple (``pop()`` returns the VALUE — indexing it raised
        KeyError, crashing the eviction loop past the cap)."""
        import tortoise.pack_manifest_store as pms
        from tortoise.pack_manifest_store import tenant_view
        sdk = _team_sdk()
        monkeypatch.setattr(pms, "_MAX_TENANT_VIEWS", 2)
        # Pre-fill the memo with two OTHER tenants (past the cap once the
        # real tenant inserts).
        with pms._TENANT_VIEWS_GUARD:
            pms._TENANT_VIEWS.clear()
            pms._TENANT_VIEWS[("ghost-a", "v1")] = {"catalog": {}, "tenant": [],
                                                    "yaml": {}, "brief": {}}
            pms._TENANT_VIEWS[("ghost-b", "v1")] = {"catalog": {}, "tenant": [],
                                                    "yaml": {}, "brief": {}}
        assert _upload(client, VALID_MANIFEST) == 201
        view = tenant_view(sdk)  # must not raise
        assert "tenant-ops:contract" in view["brief"], \
            "the current tenant's view must compile"
        with pms._TENANT_VIEWS_GUARD:
            assert len(pms._TENANT_VIEWS) <= pms._MAX_TENANT_VIEWS, \
                "fleet cap must bound the memo"
            assert any(k[0] != "ghost-a" and k[0] != "ghost-b"
                       for k in pms._TENANT_VIEWS), \
                "the real tenant's entry must survive the eviction"

    def test_view_memo_hit_and_write_invalidation(self, client):
        """The consumer path rides the tenant-view memo: cache hit on repeat
        (view identity), write invalidation (new kinds reach the tenant
        master after a :PackManifest write)."""
        from tortoise.extractor_v2 import build_master_list
        from tortoise.pack_manifest_store import tenant_view
        sdk = _team_sdk()
        v1 = tenant_view(sdk)
        assert tenant_view(sdk) is v1, "memo hit must be cached"
        assert _upload(client, VALID_MANIFEST) == 201
        v2 = tenant_view(sdk)
        assert v2 is not v1, "view must recompile after a :PackManifest write"
        master = build_master_list(sdk=sdk)
        assert "tenant-ops:contract" in master["pack_kinds"]
        # Second upload (version bump + new kind) invalidates again and the
        # new kind reaches the tenant master.
        assert _upload(client, VALID_MANIFEST_V2) == 201
        master2 = build_master_list(sdk=sdk)
        assert "tenant-ops:sla" in master2["pack_kinds"], \
            "write invalidation must surface new tenant kinds"

    def test_sha256_in_cache_key_invalidates_same_version_reupload(
            self, client, monkeypatch):
        """The sha256-in-key (#2031) is the CROSS-PROCESS staleness signal:
        with the dirty-set artificially cleared, a same-(ns, version) content
        change must still produce a new cache key (the in-process dirty-set
        alone cannot detect it)."""
        import tortoise.pack_manifest_store as pms
        from tortoise.pack_manifest_store import tenant_view
        sdk = _team_sdk()
        # Order-independence: this test couples to the process-global memo;
        # start from a clean slate so the key-set comparison is deterministic.
        with pms._TENANT_VIEWS_GUARD:
            pms._TENANT_VIEWS.clear()
            pms._TENANT_VIEW_DIRTY.clear()
        assert _upload(client, VALID_MANIFEST) == 201
        tenant_view(sdk)
        keys_before = set(pms._TENANT_VIEWS)
        # Simulate the cross-process case: this worker never saw the write.
        with pms._TENANT_VIEWS_GUARD:
            pms._TENANT_VIEW_DIRTY.discard(pms._graph_identity(sdk))
        # Same namespace + version, different content (kindDef added).
        same_version_new_content = VALID_MANIFEST.replace(
            "  objectKinds:\n  - contract\n",
            "  objectKinds:\n  - contract\n  - sla\n")
        assert _upload(client, same_version_new_content) == 201
        with pms._TENANT_VIEWS_GUARD:
            pms._TENANT_VIEW_DIRTY.discard(pms._graph_identity(sdk))
        tenant_view(sdk)
        assert set(pms._TENANT_VIEWS) != keys_before, \
            "sha256 in the cache key must invalidate same-version re-uploads"
