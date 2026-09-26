"""Cross-tenant READ isolation over the capture -> read path (#3663, B5).

Proves, by test, that a read as tenant A cannot observe tenant B's captured
data through every read surface this module covers:

- retrieval (``GET /v1/search``);
- session listing + retrieval by id (``GET /v1/sessions``, ``/v1/sessions/{id}``)
  — a guessed/leaked id must 404 across tenants;
- graph listing/scoping (MCP ``tortoise_list_graphs`` HTTP exact-name filter, the
  namespace probe ``_graph_has_org_namespace`` and its opener
  ``_open_org_graph_sdk``);
- the export path (``GET /v1/organizations/{org_id}/export``) — both the
  caller's own export content and a leaked cross-tenant org_id;
- the archive path (``GET /backups``) — the backup id (``org/graph/run``) is a
  leaked handle a listing must not cross either.

Both directions (A->B and B->A) are covered, each with the present/absent pair
asserted TOGETHER in one test: the caller's own marker must be visible AND the
other tenant's marker must be absent. A test that only asserted "0 rows" would
pass trivially when the seed never landed, so the presence half is mandatory.

The negative controls (per the B5 exit-evidence rule) live in
``test_negative_control_*``: each breaks ONE isolation seam — the shared
data-plane SDK, the archive store prefix, the export owner gate, the MCP
transport mode, or the namespace opener's graph mapping — and asserts the
paired isolation checker goes RED. A green test that cannot go red is not
evidence.

WHY THIS IS NOT A DUPLICATE — the cross-tenant coverage that already exists in
``tests/test_hosted_api.py`` and ``tests/test_mcp_http.py``:

- ``test_hosted_api.TestCrossTenantIsolation.test_team_isolation`` builds two
  random SDK namespaces and asserts, via a RAW graph query, that A's graph does
  not contain B's point. That proves namespace->graph derivation, NOT that any
  read SURFACE returns only the caller's data.
- ``test_hosted_api.TestIssueInsightAPI.test_cross_team_isolation`` covers ONE
  read surface (issue-insight) cross-team.
- ``test_mcp_http.TestGraphName.test_http_graph_name_injection_blocked`` asserts
  a user-supplied ``graph_name`` argument cannot redirect a read — and
  ``test_mcp_http.TestQuota...test_list_graphs_scoped_to_team`` asserts
  ``all(g.startswith("org_"))`` plus a ``registry``-leak check — both vacuously
  true on ``[]``. Neither has a
  seeded present/absent pair or a negative control.
- ``tests/test_hosted_backup.py`` covers cross-tenant BACKUP RESTORE rejection
  at the function level (out of this module's scope: it is not a
  capture->read surface).

None of them exercises capture -> read across the retrieval, listing, leaked-id,
graph-listing, export or archive surfaces with a seeded marker pair and a
negative control.

Harness: the canonical ``tests._http_fixtures.patched_tortoise_sdk`` seam
(beside ``tests/fake_control_plane.py``), an embedded temp DB per test module,
and the ``hosted_api`` dependency-override auth seam (shared with
``tests/test_hosted_api.py``).

Lane note: this module is a redirect carve-out (``tests/_embedded.py``
``TEST_NO_REDIRECT_STEMS``). It asserts PRODUCTION graph names (``org_{org_id}``),
which the class-level test redirect would destroy: the redirect (see the
``FalkorProjection.__init__`` block) renames every path-built graph to a
per-path ``test_*`` name, so in a URI-configured session without the exemption
no production name would exist and the scoping assertions would FAIL: the
probe's ``own=True``, the own graph in the listing, and ``_open_org_graph_sdk``
returning ``None`` for the opener. A hard RED, never a false pass. The exemption
keeps the construction embedded, so ``org_{org_id}`` really exists and the
module's own exit-evidence command stays runnable.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import os
import uuid

import pytest
from fastapi.testclient import TestClient

# #67: TORTOISE_SECRET_PEPPER is mandatory for the auth module — set before import.
os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
# Rate limiter trips 429 in full-suite runs (shared IP bucket). Tests opt out.
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

from tortoise.hosted_api import (  # noqa: I001
    app,
    get_current_org,
    get_current_user,
)
from tortoise import hosted_api as ha

from tests._http_fixtures import patched_tortoise_sdk

# ── Tenants ──────────────────────────────────────────────────────────────────
_ORG_A = "xtenant-team-a"
_ORG_B = "xtenant-team-b"
_USER_A = "9f2c1a40-0000-4a00-8000-0000000000a1"
_USER_B = "9f2c1a40-0000-4a00-8000-0000000000b1"

# The override dict shape mirrors test_hosted_api.TEST_TEAM: a legacy
# org-wide key (graph_id None, deleg NULL, scopes [] -> legacy_full_access).
_ORG_DICTS = {
    "A": {
        "org_id": _ORG_A, "key_id": "xtenant-key-a", "tier": "pro",
        "graph_id": None, "scopes": [], "legacy_full_access": True,
        "delegation_depth": None, "created_by_key_id": None,
        "max_users": 1, "max_graphs": 1, "max_points": 10000,
        "max_api_keys": 2, "max_sessions": 1000,
    },
    "B": {
        "org_id": _ORG_B, "key_id": "xtenant-key-b", "tier": "pro",
        "graph_id": None, "scopes": [], "legacy_full_access": True,
        "delegation_depth": None, "created_by_key_id": None,
        "max_users": 1, "max_graphs": 1, "max_points": 10000,
        "max_api_keys": 2, "max_sessions": 1000,
    },
}
_USER_IDS = {"A": _USER_A, "B": _USER_B}
_ORG_IDS = {"A": _ORG_A, "B": _ORG_B}
_OTHER = {"A": "B", "B": "A"}

# Surfaces that MUST carry the caller's own captured content (the presence
# half). ``sessions.detail[leaked_id]`` and ``export.leaked_org`` are
# deliberately excluded — they are absence-only (they must never resolve).
_PRESENCE_SURFACES = (
    "search", "sessions.detail[own_id]", "export.own",
)


class _TenantHarness:
    """Switchable-tenant TestClient harness with per-module unique markers.

    ``as_tenant(t)`` flips BOTH auth overrides (org + user) so the NEXT request
    is served as tenant ``t``. The dependency overrides are read per-request,
    so flipping between requests is exact and order-independent.

    Markers and session ids are unique PER MODULE (a fresh 12-hex nonce): the
    capture pipeline keeps process-global content/session dedup state, and a
    repeated literal made a later capture a silent no-op (extracted=0) under the
    server lane — which is also why ``xtenant`` is module-scoped and seeds once.
    """

    def __init__(self, client: TestClient, nonce: str):
        self.client = client
        self.nonce = nonce
        # Distinct rare single tokens (lowercase alphanumeric — FTS-safe).
        self.markers = {"A": f"xantheumalpha{nonce}", "B": f"blorptasticbeta{nonce}"}
        self.sessions = {"A": f"xtenant-session-a-{nonce}",
                         "B": f"xtenant-session-b-{nonce}"}
        self._current = "A"
        app.dependency_overrides[get_current_org] = lambda: dict(
            _ORG_DICTS[self._current])
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _USER_IDS[self._current]}

    def as_tenant(self, tenant: str) -> TestClient:
        self._current = tenant
        return self.client


@pytest.fixture(autouse=True)
def _hermetic_retrieval_leg(force_sparse_tfidf):
    """#3663 (CI carve-out RED): pin the retrieval leg to the degraded lane.

    The retrieval surface is queried with the OTHER tenant's marker — a rare token
    with no lexical overlap with the caller's own captured turns. The absence
    half is the leak check; the presence half (own marker PRESENT) is the
    anti-vacuity guard and is only satisfied by the embedded lane's DEGRADED
    fallback: ``tortoise_fts_query`` then returns no hits, retrieval drops to
    the in-memory ``fallback_snapshot`` lane, and that lane scores the WHOLE
    graph corpus — including both raw turn Points, whose content carries the
    caller's marker, at similarity 0.0, which ``search_snapshot``'s
    ``threshold=0.0`` still admits.

    That degraded lane is entered ONLY when ``raw_results`` is empty, and that
    is the environment-dependent part. In the embedded lane the FTS and
    structural legs fail, but the VECTOR leg does not need lexical overlap: it
    always returns its nearest neighbours. Sentence-transformers is installed
    in CI's ``[test,embeddings]`` extra (and in any dev env that has it), so
    the vector strategy is submitted — and the ONLY Point with a write-time
    embedding is the extractor's generic point (the per-turn Points are
    written by the capture turn loop's direct ``MERGE``, which bypasses the
    embedding write path). The vector leg therefore returns exactly that one
    marker-less point, ``raw_results`` is non-empty, the degraded all-corpus
    lane is SKIPPED, and the search response carries only the extractor's
    generic point — the caller's marker-bearing turn Points never surface, so the
    presence assertion REDs for a reason unrelated to tenancy isolation.
    With the embedder absent (or pinned off) every leg fails on a no-overlap
    query, the degraded lane runs, and the corpus-based evidence is
    deterministic.

    ``force_sparse_tfidf`` (tests/conftest.py, the #2573/#2772 pattern) pins
    the leg so the test no longer depends on whether the embedder happens to
    be importable/loaded in the process — alone or after other carve-out files
    have warmed the ``EmbeddingModel`` singleton. Assertions are unchanged:
    the own-marker-present / other-marker-absent contract is still enforced,
    and the negative controls still drive the same seam.
    """
    return force_sparse_tfidf


@pytest.fixture(scope="module")
def xtenant(tmp_path_factory):
    """Two tenants A and B on one embedded temp DB (canonical SDK seam).

    MODULE-scoped and seeded ONCE: the v2 extraction pipeline carries
    process-global consolidation state, so re-capturing the same shape in a
    later test made the second capture a silent no-op under the server lane.
    All nine tests therefore share one seeded fixture (the negative controls
    only re-read; none re-captures).
    """
    # Offline M2 extraction (the #822 LLM-mock test seam) — no network.
    os.environ.setdefault("TORTOISE_SESSION_LLM_MOCK", "1")
    db_path = str(tmp_path_factory.mktemp("xtenant") / "xtenant.db")

    with patched_tortoise_sdk(db_path):
        # Hold the seed SDKs for the whole module. Under a server URI
        # ``_make_sdk`` takes its early URI branch and creates NO keepalive
        # anchor, so a dropped seed SDK's close-on-GC would SHUTDOWN NOSAVE
        # the embedded server and the next read would respawn it EMPTY (the
        # #1475/#2090 data-loss class; test_export_delete._seed_graph holds
        # refs for the same reason). Released in the finally below.
        seed_sdks: list = []

        # Registry control plane: a Team + an ACTIVE owner Membership per
        # tenant. Export/session-user authz reads these; capture reads the
        # Team node's onboarding_state (session_recording default-ON).
        reg_sdk = ha._make_sdk(namespace="registry")
        seed_sdks.append(reg_sdk)
        reg = reg_sdk._get_registry()
        for t in ("A", "B"):
            reg.query("CREATE (t:Team {id:$id, name:$name, tier:'pro'})",
                      params={"id": _ORG_IDS[t], "name": _ORG_IDS[t]})
            reg.query(
                "CREATE (m:Membership {id:$mid, user_id:$uid, org_id:$tid, "
                "role:'owner', status:'active', "
                "joined_at:'2026-08-01T00:00:00Z'})",
                params={"mid": f"mem-{t}", "uid": _USER_IDS[t],
                        "tid": _ORG_IDS[t]})

        # Session-capture consent + self-verify (mirrors test_hosted_api's
        # fixture). The seed is asserted here because neither downstream gate
        # would catch a silent no-op: an ABSENT onboarding_state is not fatal
        # at all (``_get_onboarding_state`` auto-initializes the defaults,
        # where ``session_recording`` is True), and a recording flag that read
        # back False surfaces at capture as a 409 state-conflict — the
        # recording gate's status, NOT the old 403 consent error.
        for t in ("A", "B"):
            state = ha._update_onboarding_state(
                _ORG_IDS[t], session_recording=True)
            assert state.get("session_recording") is True, (t, state)
            assert ha._get_onboarding_state(_ORG_IDS[t]).get(
                "session_recording") is True, f"consent seed invisible for {t}"

        try:
            with TestClient(app) as tc:
                harness = _TenantHarness(tc, uuid.uuid4().hex[:12])
                # Exposed for the MCP-transport probe: the SAME registry SDK
                # the seed wrote through, so a key minted there resolves in
                # OrgResolutionMiddleware (same db/URI).
                harness.registry_sdk = reg_sdk
                _seed_both_tenants(harness)
                yield harness
        finally:
            while seed_sdks:
                with contextlib.suppress(Exception):
                    seed_sdks.pop().close()


# ── Capture (the "capture" half of capture -> read) ──────────────────────────

def _capture(h: _TenantHarness, tenant: str) -> None:
    """Capture a session as `tenant` whose transcript carries its marker."""
    client = h.as_tenant(tenant)
    marker = h.markers[tenant]
    session_id = h.sessions[tenant]
    r = client.post("/v1/sessions", json={
        "session_id": session_id,
        "harness": "pi",
        "conversation": [
            {"role": "user",
             "content": f"Remember this fact: the {marker} ledger is due."},
            {"role": "assistant",
             "content": f"Noted, the {marker} ledger is due."},
        ],
    })
    assert r.status_code == 200, f"capture as {tenant} failed: {r.status_code} {r.text}"
    body = r.json()
    assert body["session_id"] == session_id, body
    assert body["extracted"] >= 1, f"capture as {tenant} extracted nothing: {body}"


def _seed_both_tenants(h: _TenantHarness) -> None:
    _capture(h, "A")
    _capture(h, "B")


# ── Read surfaces ────────────────────────────────────────────────────────────

def _read_surfaces(h: _TenantHarness, tenant: str) -> dict[str, str]:
    """Marker-bearing read surfaces, read AS `tenant` over the OTHER's
    captured content. Values are the exact wire payloads, so presence/absence
    is tested, never inferred.

    Session LISTING is metadata-only (no content), so it is asserted by id in
    the caller, not here. Status-based contracts (leaked-id 404, leaked-org
    403) are observational here and asserted by the caller too.
    """
    other = _OTHER[tenant]
    client = h.as_tenant(tenant)
    out: dict[str, str] = {}

    # 1. retrieval.
    r = client.get("/v1/search", params={"q": h.markers[other]})
    assert r.status_code == 200, f"search as {tenant}: {r.status_code} {r.text}"
    out["search"] = json.dumps(r.json())

    # 2. session listing (id-based; kept for the caller's id/status checks).
    r = client.get("/v1/sessions")
    assert r.status_code == 200, f"list sessions as {tenant}: {r.text}"
    out["sessions.list"] = json.dumps(r.json())

    # 3. session retrieval by id — own (content) and the LEAKED other id.
    out["sessions.detail[leaked_id]"] = client.get(
        f"/v1/sessions/{h.sessions[other]}").text
    out["sessions.detail[own_id]"] = client.get(
        f"/v1/sessions/{h.sessions[tenant]}").text

    # 4. export of the caller's OWN org (content).
    out["export.own"] = client.get(
        f"/v1/organizations/{_ORG_IDS[tenant]}/export").text
    # 5. export of the OTHER tenant's org — a guessed/leaked org_id.
    out["export.leaked_org"] = client.get(
        f"/v1/organizations/{_ORG_IDS[other]}/export").text

    return out


def _split_readouts(readouts: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Split into (leak surfaces, presence surfaces).

    ``sessions.list`` is metadata-only (no content) — its contract is id-based
    and is asserted directly by the caller, so it is dropped here.
    """
    leak = {k: v for k, v in readouts.items() if k != "sessions.list"}
    presence = {k: leak[k] for k in _PRESENCE_SURFACES}
    return leak, presence


def _read_graph_listing(tenant: str) -> str:
    """MCP ``tortoise_list_graphs`` over the HTTP transport.

    ``list_graphs`` returns the SERVER-WIDE graph list; the HTTP branch must
    filter it down to the calling tenant's own ``org_{org_id}`` / legacy
    ``team_{org_id}`` names by EXACT membership (``g in own``), not by prefix.
    """
    from tortoise import mcp_server
    from tortoise.mcp_auth import _current_org_id, _transport_mode

    tok_id = _current_org_id.set(_ORG_IDS[tenant])
    tok_mode = _transport_mode.set("http")
    try:
        return json.dumps(mcp_server.tortoise_list_graphs())
    finally:
        _transport_mode.reset(tok_mode)
        _current_org_id.reset(tok_id)


def _assert_reads_are_isolated(leak_readouts: dict[str, str],
                               presence_readouts: dict[str, str], *,
                               own: str, other: str) -> None:
    """The single isolation checker: other's marker ABSENT, own's PRESENT.

    Two surface sets because they answer two different questions:

    - ``leak_readouts`` — every surface that COULD carry another tenant's
      content (including the leaked-id/leaked-org responses). The other
      tenant's marker must be absent from all of them.
    - ``presence_readouts`` — the surfaces that SHOULD carry the caller's own
      captured content. The caller's marker must be present in all of them.

    Both halves are required: absence alone is vacuous when the seed never
    landed (the B5 false-PASS rule). The leak assertion fires first so a
    routed read reports the cross-tenant breach, not a confusing "own marker
    missing".
    """
    for surface, text in leak_readouts.items():
        assert other not in text, (
            f"CROSS-TENANT LEAK via {surface}: tenant marker {other!r} visible "
            f"in {surface} read")
    for surface, text in presence_readouts.items():
        assert own in text, (
            f"seed/read path broken via {surface}: own marker {own!r} MISSING "
            f"from {surface} read (the absence assertion above was vacuous)")


def _assert_session_id_contract_isolated(client, *, own_session: str,
                                         other_session: str, tenant: str,
                                         other: str) -> None:
    """The session-id contract: leaked cross-tenant id 404, own id 200.

    Split out so the negative control can drive THIS checker (leak first,
    mirroring ``_assert_reads_are_isolated``) against a deliberately routed
    data-plane seam.
    """
    r_leak = client.get(f"/v1/sessions/{other_session}")
    assert r_leak.status_code == 404, (
        f"CROSS-TENANT LEAK: tenant {tenant} read session "
        f"{other_session!r} (belonging to {other}): "
        f"{r_leak.status_code} {r_leak.text}")
    r_own = client.get(f"/v1/sessions/{own_session}")
    assert r_own.status_code == 200, (
        f"own session {own_session!r} unreadable as {tenant}: "
        f"{r_own.status_code} {r_own.text}")


def _assert_export_org_contract_isolated(client, *, tenant: str,
                                         other: str) -> None:
    """The export-org contract: leaked org_id 403, own export 200.

    Split out so the negative control can drive THIS checker with the
    owner-RBAC gate bypassed.
    """
    r_leak = client.get(f"/v1/organizations/{_ORG_IDS[other]}/export")
    assert r_leak.status_code == 403, (
        f"CROSS-TENANT LEAK: tenant {tenant} exported {_ORG_IDS[other]}: "
        f"{r_leak.status_code} {r_leak.text}")
    r_own = client.get(f"/v1/organizations/{_ORG_IDS[tenant]}/export")
    assert r_own.status_code == 200, (
        f"own export as {tenant}: {r_own.status_code} {r_own.text}")


def _assert_graph_listing_isolated(listing: str, *, tenant: str) -> None:
    """The MCP graph-listing scoping checker: own name PRESENT, other's ABSENT.

    Split out so the negative control can drive THIS checker against a
    deliberately unfiltered listing (the transport-mode break).
    """
    own_graph = f"org_{_ORG_IDS[tenant]}"
    other_graph = f"org_{_ORG_IDS[_OTHER[tenant]]}"
    assert own_graph in listing, (
        f"graph listing as {tenant} missing own graph {own_graph}: {listing}")
    assert other_graph not in listing, (
        f"CROSS-TENANT LEAK via graph listing: {other_graph} visible to "
        f"{tenant}: {listing}")


def _assert_namespace_opener_isolated(probe_text: str, *, tenant: str,
                                      markers: dict[str, str]) -> None:
    """The namespace-opener checker: other's marker ABSENT, own's PRESENT.

    Split out so the negative control can drive THIS checker against a
    deliberately misrouted opener.
    """
    other = _OTHER[tenant]
    assert markers[other] not in probe_text, (
        f"CROSS-TENANT LEAK via namespace opener: "
        f"{markers[other]!r} visible to {tenant}")
    assert markers[tenant] in probe_text, (
        f"namespace opener for {tenant} did not return own marker")


def _namespace_probe(tenant: str) -> str:
    """The namespace existence probe, plus the phantom-org check.

    ``_graph_has_org_namespace`` probes the server-wide graph list for the
    tenant's own prefixes. It must say True for a tenant whose graph exists
    and must NOT blanket-True a never-existed org (the probe is scoped, not a
    constant).
    """
    owned = ha._graph_has_org_namespace(_ORG_IDS[tenant])
    phantom = ha._graph_has_org_namespace(f"no-such-org-{tenant}")
    return f"own={owned} phantom={phantom}"


def _namespace_probe_read(tenant: str) -> str:
    """Read through the namespace OPENER the probe gates (``_open_org_graph_sdk``).

    The opener addresses the graph name the probe verified; a wrong-tenant
    mapping here is exactly the #3543 rename failure class. Returns the
    tenant's Point contents, so the shared marker checker can inspect it.
    """
    sdk = ha._open_org_graph_sdk(_ORG_IDS[tenant])
    assert sdk is not None, (
        f"_open_org_graph_sdk returned None for existing org {_ORG_IDS[tenant]}")
    try:
        rows = sdk._get_proj().g.query(
            "MATCH (p:Point) WHERE p.content IS NOT NULL RETURN p.content"
        ).result_set
        return "\n".join(str(r[0]) for r in rows)
    finally:
        sdk.close()


# ── The isolation test (both directions, present+absent together) ─────────────

def test_captured_reads_do_not_cross_tenants_both_directions(
        xtenant):
    for tenant in ("A", "B"):
        other = _OTHER[tenant]
        client = xtenant.as_tenant(tenant)
        readouts = _read_surfaces(xtenant, tenant)
        leak_readouts, presence_readouts = _split_readouts(readouts)
        _assert_reads_are_isolated(
            leak_readouts, presence_readouts,
            own=xtenant.markers[tenant], other=xtenant.markers[other])

        # Session listing is metadata-only: own session id present, other's
        # absent (the id contract, checked independently of content).
        own_session = xtenant.sessions[tenant]
        other_session = xtenant.sessions[other]
        assert own_session in readouts["sessions.list"], (
            f"session listing as {tenant} missing own session {own_session}: "
            f"{readouts['sessions.list']}")
        assert other_session not in readouts["sessions.list"], (
            f"CROSS-TENANT LEAK via session listing: {other_session} visible "
            f"to {tenant}: {readouts['sessions.list']}")

        # The id contract: leaked cross-tenant id 404, own session readable.
        _assert_session_id_contract_isolated(
            client, own_session=own_session, other_session=other_session,
            tenant=tenant, other=other)

        # The export-org contract: leaked org_id 403, own export 200.
        _assert_export_org_contract_isolated(client, tenant=tenant,
                                             other=other)

        # Graph listing: caller's own graph name present, other's absent.
        _assert_graph_listing_isolated(_read_graph_listing(tenant),
                                       tenant=tenant)

        # Namespace probe -> opener: the probe says the tenant's graph exists
        # and the opener reads ONLY that tenant's content.
        probe = _namespace_probe(tenant)
        assert "own=True" in probe, probe
        assert "phantom=False" in probe, probe
        _assert_namespace_opener_isolated(
            _namespace_probe_read(tenant), tenant=tenant,
            markers=xtenant.markers)


# ── Archive (backup) retrieval — the leaked-id handle ───────────────────────

# Per-tenant archive graph ids (the leaked handle the listing must not cross).
_ARCH_GID = {"A": "g-xtenant-arch-a", "B": "g-xtenant-arch-b"}


def _seed_archive_store() -> object:
    """A store holding ONE archive run per tenant, under that tenant's prefix."""
    from tortoise.hosted_backup import MemoryStorage

    store = MemoryStorage()
    for t in ("A", "B"):
        org = _ORG_IDS[t]
        store.upload(
            f"backups/{org}/{_ARCH_GID[t]}/run{t}/manifest.json",
            json.dumps({
                "backup_id": f"{org}/{_ARCH_GID[t]}/run{t}",
                "created_at": "2026-09-01T00:00:00Z",
                "node_count": 3,
            }).encode(),
        )
    return store


def _read_archive_listings(xtenant) -> dict[str, str]:
    seen: dict[str, str] = {}
    for t in ("A", "B"):
        r = xtenant.as_tenant(t).get("/backups")
        assert r.status_code == 200, f"backups as {t}: {r.status_code} {r.text}"
        seen[t] = json.dumps(r.json())
    return seen


def _assert_archive_listing_isolated(seen: dict[str, str]) -> None:
    """The archive scoping checker: own run PRESENT, other's ABSENT.

    Split out so the negative control can drive THIS checker (both halves)
    against a deliberately un-scoped store.
    """
    assert _ARCH_GID["A"] in seen["A"], (
        f"own archive run missing for A: {seen['A']}")
    assert _ARCH_GID["B"] in seen["B"], (
        f"own archive run missing for B: {seen['B']}")
    assert _ARCH_GID["B"] not in seen["A"], (
        "CROSS-TENANT LEAK via backup archive listing: "
        f"{_ARCH_GID['B']} visible to A: {seen['A']}")
    assert _ARCH_GID["A"] not in seen["B"], (
        "CROSS-TENANT LEAK via backup archive listing: "
        f"{_ARCH_GID['A']} visible to B: {seen['B']}")


def test_backup_archive_listing_does_not_cross_tenants(xtenant, monkeypatch):
    """``GET /backups`` lists ONLY the caller's own archive pool — both
    directions, presence AND absence asserted together.

    The backup id (``org/graph/run``) is the leaked handle the B5 gate names:
    tenant A's archive run must be visible to A and INVISIBLE to B, and vice
    versa. The presence half keeps a vacuously-empty listing from passing —
    the scoping read has to be shown to actually return the caller's run.
    """
    monkeypatch.setattr(ha, "_backup_storage", _seed_archive_store)
    _assert_archive_listing_isolated(_read_archive_listings(xtenant))


# ── Negative control ─────────────────────────────────────────────────────────

def test_negative_control_routed_reads_are_detected(xtenant, monkeypatch):
    """The isolation checker MUST go red when the read seam points at the other
    tenant. This is the control that keeps the green test from being vacuous.

    Reuses the module's seeded tenants (no re-capture — the v2 extractor's
    process-global consolidation state makes a second identical capture a
    silent no-op under the server lane). Mutates the ``_data_sdk`` seam — the
    one the retrieval and both session surfaces share — so tenant A's reads
    open tenant B's graph, then asserts the shared checker raises with the
    cross-tenant message.

    Scope: ``_data_sdk`` ONLY. The export path (``_make_sdk`` by graph name,
    behind its own owner gate), the archive store prefix, the MCP listing
    filter and the namespace opener each have their own negative control
    below — this control does not exercise them.
    """
    real_make_sdk = ha._make_sdk

    def _routed_data_sdk(org):
        # Point A's reads at B's graph (the injected isolation break).
        ns = _ORG_B if org["org_id"] == _ORG_A else org["org_id"]
        return real_make_sdk(namespace=ns)

    monkeypatch.setattr(ha, "_data_sdk", _routed_data_sdk)

    readouts = _read_surfaces(xtenant, "A")
    leak_readouts, presence_readouts = _split_readouts(readouts)
    with pytest.raises(AssertionError) as excinfo:
        _assert_reads_are_isolated(
            leak_readouts, presence_readouts,
            own=xtenant.markers["A"], other=xtenant.markers["B"])
    assert "CROSS-TENANT LEAK" in str(excinfo.value), (
        "negative control did not fire the leak assertion: "
        f"{excinfo.value}")


def test_negative_control_archive_scoping_is_detected(xtenant, monkeypatch):
    """The archive scoping checker MUST go red when the store ignores the org
    prefix (the scoping-break class ``list_backups`` guards against).

    A prefix-blind storage returns every tenant's manifest for every org, so
    the shared archive checker must raise the cross-tenant message — proof
    that the archive test's absence half is not vacuous.
    """
    real = _seed_archive_store()

    class _PrefixBlind(type(real)):  # type: ignore[misc, valid-type]
        def list(self, prefix: str = ""):
            return super().list("")  # deliberately ignore the org scope

    blind = _PrefixBlind()
    blind._objects = dict(real._objects)  # carry the seeded runs over
    monkeypatch.setattr(ha, "_backup_storage", lambda: blind)

    seen = _read_archive_listings(xtenant)
    with pytest.raises(AssertionError) as excinfo:
        _assert_archive_listing_isolated(seen)
    assert "CROSS-TENANT LEAK" in str(excinfo.value), (
        f"archive negative control did not fire: {excinfo.value}")


def test_negative_control_leaked_session_id_is_detected(xtenant, monkeypatch):
    """P2-1 control: the session-id contract MUST go red when the data-plane
    seam routes A's reads at B's graph.

    Same ``_data_sdk`` seam as the routed-reads control (so a misconfigured
    fixture reds instead of false-passing), but pinning the SPECIFIC
    ``sessions.detail[leaked_id]`` 404 contract rather than the shared marker
    checker. With the seam broken the leaked id RESOLVES (200 carrying B's
    marker) where the module asserts a hard 404.
    """
    real_make_sdk = ha._make_sdk

    def _routed_data_sdk(org):
        ns = _ORG_B if org["org_id"] == _ORG_A else org["org_id"]
        return real_make_sdk(namespace=ns)

    monkeypatch.setattr(ha, "_data_sdk", _routed_data_sdk)

    client = xtenant.as_tenant("A")
    leaked = client.get(f"/v1/sessions/{xtenant.sessions['B']}")
    assert leaked.status_code == 200, (
        "negative control did not break the session-detail seam (leaked id "
        f"still {leaked.status_code}) — cannot false-pass: {leaked.text}")
    assert xtenant.markers["B"] in leaked.text, (
        f"routed detail read carried no B marker: {leaked.text}")
    with pytest.raises(AssertionError) as excinfo:
        _assert_session_id_contract_isolated(
            client, own_session=xtenant.sessions["A"],
            other_session=xtenant.sessions["B"], tenant="A", other="B")
    assert "CROSS-TENANT LEAK" in str(excinfo.value), (
        f"leaked-session control did not fire: {excinfo.value}")


def test_negative_control_export_owner_gate_is_detected(xtenant, monkeypatch):
    """P2-1 control: the export-org contract MUST go red when the owner gate
    is a no-op.

    ``_require_owner`` is the ONLY authz gate on the export route; bypassing
    it lets tenant A export tenant B's graph (200 carrying B's marker) where
    the module asserts a hard 403.
    """
    async def _noop_require_owner(user_id, org_id, **kwargs):
        return {"org_id": org_id, "role": "owner"}

    monkeypatch.setattr(ha, "_require_owner", _noop_require_owner)

    client = xtenant.as_tenant("A")
    leaked = client.get(f"/v1/organizations/{_ORG_B}/export")
    assert leaked.status_code == 200, (
        "negative control did not bypass the owner gate (leaked org export "
        f"still {leaked.status_code}) — cannot false-pass: {leaked.text}")
    assert xtenant.markers["B"] in leaked.text, (
        f"export leaked no B marker: {leaked.text}")
    with pytest.raises(AssertionError) as excinfo:
        _assert_export_org_contract_isolated(client, tenant="A", other="B")
    assert "CROSS-TENANT LEAK" in str(excinfo.value), (
        f"export control did not fire: {excinfo.value}")


def test_negative_control_graph_listing_scoping_is_detected(xtenant,
                                                            monkeypatch):
    """P2-1 control: the MCP graph-listing checker MUST go red when the HTTP
    filter stops scoping.

    ``tortoise_list_graphs`` filters only when the transport mode it sees is
    ``"http"``; with that seam broken the SERVER-WIDE list — including the
    other tenant's ``org_{org_id}`` name — is returned.
    """
    from tortoise import mcp_server

    monkeypatch.setattr(
        mcp_server, "_transport_mode",
        contextvars.ContextVar("xtenant_unfiltered_mode", default="stdio"))

    listing = _read_graph_listing("A")
    assert f"org_{_ORG_B}" in listing, (
        "negative control did not leak the other tenant's graph name: "
        f"{listing}")
    with pytest.raises(AssertionError) as excinfo:
        _assert_graph_listing_isolated(listing, tenant="A")
    assert "CROSS-TENANT LEAK via graph listing" in str(excinfo.value), (
        f"graph-listing control did not fire: {excinfo.value}")


def test_negative_control_namespace_opener_misroute_is_detected(xtenant,
                                                                monkeypatch):
    """P2-1 control: the namespace-opener checker MUST go red when
    ``_make_sdk`` maps A's org to B's.

    The opener addresses the graph name the probe verified; a wrong-tenant
    mapping (the #3543 rename failure class) reads the OTHER tenant's Point
    contents — B's marker present, A's absent.
    """
    real_make_sdk = ha._make_sdk

    def _misrouted_make_sdk(*, namespace=None, graph_name=None):
        if namespace == _ORG_A:
            namespace = _ORG_B
        return real_make_sdk(namespace=namespace, graph_name=graph_name)

    monkeypatch.setattr(ha, "_make_sdk", _misrouted_make_sdk)

    probe_text = _namespace_probe_read("A")
    assert xtenant.markers["B"] in probe_text, (
        "negative control did not misroute the opener (no B marker): "
        f"{probe_text}")
    with pytest.raises(AssertionError) as excinfo:
        _assert_namespace_opener_isolated(
            probe_text, tenant="A", markers=xtenant.markers)
    assert "CROSS-TENANT LEAK via namespace opener" in str(excinfo.value), (
        f"namespace-opener control did not fire: {excinfo.value}")


# ── Transport wiring (P2-2) ──────────────────────────────────────────────────

def test_mcp_transport_sets_org_contextvar_from_key_auth(xtenant):
    """P2-2: prove the MCP HTTP transport populates ``_current_org_id``.

    ``_read_graph_listing`` sets the contextvars itself and calls the tool
    function, so it proves the FILTER but cannot prove the transport ever
    sets them. This drives the REAL ``OrgResolutionMiddleware`` with a real
    registry-minted key over a minimal ASGI app and asserts the contextvar
    the listing filter reads is set by the key auth — the production path.
    (The full ``/mcp`` JSON-RPC path is covered by
    ``tests/test_mcp_http.py``.)
    """
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from tortoise.mcp_auth import OrgResolutionMiddleware, _current_org_id

    key = xtenant.registry_sdk.apikey_create(
        _ORG_A, "xtenant-mcp-transport-probe")["api_key"]

    async def _echo_org(request):
        return JSONResponse({"org_id": _current_org_id.get()})

    # No ambient value: the middleware under test is the ONLY thing that can
    # set this, so the assertion below is causal, not incidental.
    assert _current_org_id.get() is None, (
        f"leaked contextvar before probe: {_current_org_id.get()!r}")

    inner = Starlette(routes=[Route("/probe", _echo_org, methods=["POST"])])
    wrapped = OrgResolutionMiddleware(inner, registry_sdk=xtenant.registry_sdk)
    with TestClient(wrapped) as mcp_tc:
        r = mcp_tc.post("/probe", headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        })
    assert r.status_code == 200, f"transport probe: {r.status_code} {r.text}"
    assert r.json()["org_id"] == _ORG_A, (
        "OrgResolutionMiddleware did not populate _current_org_id from the "
        f"key auth: {r.json()}")
