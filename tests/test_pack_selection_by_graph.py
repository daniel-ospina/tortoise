"""#2728 — per-graph pack SELECTION: the state model, the write core, and the
management REST surface.

**Class B** per the lane's test doctrine: the decision (the scope comment's
reconciled acceptance criteria) is already made and stated as testable
indicators, so the tests assert the CONTRACT the decision names, not the code
that happens to implement it. Every mechanical test answers ``FAIL-ON``
(what value makes this fail, and is that value reachable from this fixture?)
inline.

Indicators under test (from #2728's own — not #2714's — acceptance criteria):
  1. ``GET``/``PUT /v1/graphs/{graph_id}/packs`` return and mutate the
     per-graph set with correct authz, audit, optimistic concurrency (409 on
     revision mismatch) and cross-graph isolation;
  2. the ``(:PackSelection)`` marker is written on first explicit
     configuration, suppresses the self-heal, and increments ``revision`` on
     every effective mutation;
  3. ``resolved_pack_set`` returns the status-aware active namespace set AND
     the revision (the #2714-ready allowlist).

The contract with #2714 is asserted here too, because #2728 must not break it:
an UNCONFIGURED graph keeps the legacy union (no restriction), and a namespace
whose kinds are already IN THE GRAPH stays writable after it is disabled.
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import pytest

from tests._http_fixtures import patched_tortoise_sdk
from tortoise import pack_state as ps
from tortoise.pack_state import (
    InvalidPackStatus,
    PackRevisionConflict,
    UnknownPackNamespace,
    ensure_tenant_packs,
    get_tenant_packs,
    graph_installed_namespaces,
    read_pack_selection,
    resolved_pack_set,
    set_graph_packs,
)
from tortoise.sdk import TortoiseSDK

DEV = "dev"
MARKETING = "marketing"
_U1 = "9f2c1a40-0000-4a00-8000-000000000009"


def _sdk(tmp_path, namespace: str) -> TortoiseSDK:
    return TortoiseSDK(db_path=str(tmp_path / f"{namespace}.db"),
                       namespace=namespace)


def _raw_installs(sdk) -> list[tuple]:
    return sdk._get_proj().g.query(
        "MATCH (p:PackInstall) RETURN p.namespace, p.status ORDER BY p.namespace"
    ).result_set


# ── the state model ───────────────────────────────────────────────────────


class TestResolvedPackSet:
    def test_unconfigured_graph_has_no_restriction(self, tmp_path):
        """FAIL-ON: a fresh graph must NOT resolve to an empty allowlist.

        ``namespaces is None`` is the #2714 indicator-3 back-compat answer;
        an implementation that returned ``frozenset()`` here would gate every
        pre-#318/self-hosted graph down to core-only.
        """
        sdk = _sdk(tmp_path, "test_sel_unconfigured")
        resolved = resolved_pack_set(sdk)
        assert resolved.namespaces is None
        assert resolved.configured is False
        assert resolved.revision == 0
        assert read_pack_selection(sdk) is None

    def test_first_put_writes_the_marker_even_for_an_empty_set(self, tmp_path):
        """The marker's PRESENCE is the payload: it is what makes an explicit
        empty set expressible and distinguishable from "never configured".

        FAIL-ON: ``frozenset()`` (configured) vs ``None`` (not configured) —
        returning ``None`` here is exactly the "remove-all is unreachable"
        defect #2728 exists to fix.
        """
        sdk = _sdk(tmp_path, "test_sel_empty")
        resolved = set_graph_packs(sdk, packs=[], expected_revision=0)
        assert resolved.namespaces == frozenset()
        assert resolved.configured is True
        assert resolved.revision == 1
        assert read_pack_selection(sdk).revision == 1

    def test_configuring_pins_the_outset_and_suppresses_the_self_heal(
            self, tmp_path, monkeypatch):
        """The self-heal must not re-seed a deliberately configured graph.

        FAIL-ON: if the self-heal still fired, ``get_tenant_packs`` would
        return the starter set here instead of ``[]``.
        """
        monkeypatch.delenv("PACK_STATE_DISABLE_SELF_HEAL", raising=False)
        sdk = _sdk(tmp_path, "test_sel_no_selfheal")
        set_graph_packs(sdk, packs=[], expected_revision=0)
        assert get_tenant_packs(sdk) == []
        assert graph_installed_namespaces(sdk) == frozenset()

    def test_disable_takes_the_namespace_out_of_the_set_but_keeps_the_record(
            self, tmp_path):
        """``disabled`` = approved but extraction-off.

        FAIL-ON: a resolver that ignored status would keep ``marketing`` in
        the set (the pre-#2728 additive behaviour).
        """
        sdk = _sdk(tmp_path, "test_sel_disable")
        set_graph_packs(sdk, packs=[{"namespace": DEV}, {"namespace": MARKETING}],
                        expected_revision=0)
        resolved = set_graph_packs(
            sdk,
            packs=[{"namespace": DEV},
                   {"namespace": MARKETING, "status": "disabled"}],
            expected_revision=1)
        assert resolved.namespaces == frozenset({DEV})
        assert resolved.revision == 2
        listed = {p["namespace"]: p["status"] for p in get_tenant_packs(sdk)}
        assert listed == {DEV: "active", MARKETING: "disabled"}
        raw = dict(_raw_installs(sdk))
        assert raw[MARKETING] == "disabled"  # record survives

    def test_omission_tombstones_and_removed_is_absent_from_the_list(
            self, tmp_path):
        """Removal is expressed by OMISSION; the record becomes a tombstone
        and leaves the introspection list.

        FAIL-ON: if ``removed`` were still listed, the user would see a pack
        they removed; if the node were deleted, the audit trail would vanish.
        """
        sdk = _sdk(tmp_path, "test_sel_remove")
        set_graph_packs(sdk, packs=[{"namespace": DEV}, {"namespace": MARKETING}],
                        expected_revision=0)
        resolved = set_graph_packs(sdk, packs=[{"namespace": DEV}],
                                   expected_revision=1)
        assert resolved.namespaces == frozenset({DEV})
        assert [p["namespace"] for p in get_tenant_packs(sdk)] == [DEV]
        assert dict(_raw_installs(sdk))[MARKETING] == "removed"

    def test_removed_status_is_not_settable(self, tmp_path):
        """``removed`` is a derived tombstone, never an input status."""
        sdk = _sdk(tmp_path, "test_sel_badstatus")
        with pytest.raises(InvalidPackStatus):
            set_graph_packs(
                sdk, packs=[{"namespace": DEV, "status": "removed"}],
                expected_revision=0)

    def test_unknown_namespace_is_refused(self, tmp_path):
        """A namespace that is neither in the catalog nor already recorded is
        refused — a typo must not mint a phantom approval."""
        sdk = _sdk(tmp_path, "test_sel_unknown")
        with pytest.raises(UnknownPackNamespace):
            set_graph_packs(sdk, packs=[{"namespace": "not-a-pack"}],
                            expected_revision=0)

    def test_stale_expected_revision_conflicts_with_the_current_one(
            self, tmp_path):
        """Optimistic concurrency: the refusal carries the ACTUAL revision so
        a client can reload and retry without a second read."""
        sdk = _sdk(tmp_path, "test_sel_conflict")
        set_graph_packs(sdk, packs=[{"namespace": DEV}], expected_revision=0)
        with pytest.raises(PackRevisionConflict) as exc:
            set_graph_packs(sdk, packs=[{"namespace": MARKETING}],
                            expected_revision=0)
        assert exc.value.current == 1

    def test_lost_cas_race_refuses_and_writes_nothing(self, tmp_path,
                                                      monkeypatch):
        """The conditional bump IS the cross-worker compare-and-swap: when a
        peer wins between our read and our write, the loser is refused AND has
        mutated nothing. A record-first order would leave a partial write —
        the 409 would sit on top of a half-applied set.

        FAIL-ON: the monkeypatch forces the race window open; the last three
        asserts fail if any install record was written before the refusal.
        """
        sdk = _sdk(tmp_path, "test_sel_cas")
        set_graph_packs(sdk, packs=[{"namespace": DEV},
                                    {"namespace": MARKETING}],
                        expected_revision=0)
        monkeypatch.setattr(ps, "_bump_selection", lambda *a, **k: False)
        with pytest.raises(PackRevisionConflict):
            set_graph_packs(
                sdk,
                packs=[{"namespace": DEV},
                       {"namespace": MARKETING, "status": "disabled"}],
                expected_revision=1)
        assert read_pack_selection(sdk).revision == 1
        assert dict(_raw_installs(sdk)) == {DEV: "active",
                                            MARKETING: "active"}
        assert resolved_pack_set(sdk).namespaces == frozenset({DEV, MARKETING})

    def test_noop_put_keeps_the_revision(self, tmp_path):
        """An idempotent retry must not bump the concurrency token, or a
        caller retrying after a network blip would invalidate its own read."""
        sdk = _sdk(tmp_path, "test_sel_noop")
        set_graph_packs(sdk, packs=[{"namespace": DEV}], expected_revision=0)
        again = set_graph_packs(sdk, packs=[{"namespace": DEV}],
                                expected_revision=1)
        assert again.revision == 1

    def test_changes_are_isolated_to_their_graph(self, tmp_path):
        """A change on graph A must leave graph B untouched.

        FAIL-ON: graph B is seeded with ``marketing``; a leak would show it
        losing the namespace when A is narrowed.
        """
        a = _sdk(tmp_path, "test_sel_a")
        b = _sdk(tmp_path, "test_sel_b")
        ensure_tenant_packs(b, starter=[MARKETING])
        set_graph_packs(a, packs=[{"namespace": DEV}], expected_revision=0)
        assert resolved_pack_set(a).namespaces == frozenset({DEV})
        assert resolved_pack_set(b).namespaces == frozenset({MARKETING})

    def test_unconfigured_graph_keeps_every_status_in_the_set(self, tmp_path):
        """#2714's contract, preserved verbatim for an UNCONFIGURED graph:
        the "keep writable union" arm (any status stays) still applies when no
        marker exists. This is the regression guard for #2728 vs #2714."""
        sdk = _sdk(tmp_path, "test_sel_legacy_union")
        sdk._get_proj().g.query(
            "CREATE (:PackInstall {namespace: $ns, version: '0.1.0', "
            "status: 'removed', source: 'custom'})", params={"ns": MARKETING})
        resolved = resolved_pack_set(sdk)
        assert resolved.configured is False
        assert MARKETING in resolved.namespaces
        assert MARKETING in graph_installed_namespaces(sdk)

    def test_data_union_keeps_a_disabled_namespaces_kinds_writable(
            self, tmp_path):
        """A namespace whose kinds are ALREADY IN THE GRAPH stays in the
        resolved set after it is disabled — otherwise historical data becomes
        un-updatable (#2714 indicator 2 inverted)."""
        sdk = _sdk(tmp_path, "test_sel_data_union")
        sdk._get_proj().g.query(
            "CREATE (n:Point {objectKind: $k})",
            params={"k": f"{MARKETING}:contentBrief"})
        set_graph_packs(sdk, packs=[{"namespace": DEV}], expected_revision=0)
        resolved = resolved_pack_set(sdk)
        assert MARKETING in resolved.namespaces


# ── the management REST surface ───────────────────────────────────────────


@pytest.fixture
def sel_client(monkeypatch):
    """Registry-mode TestClient with a temp embedded DB (selfhost lane)."""
    from fastapi.testclient import TestClient

    from tortoise.hosted_api import app
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "sel.db")
        with patched_tortoise_sdk(db_path), TestClient(app) as tc:
            yield tc, db_path


def _seed_org(db_path: str, org_id: str) -> dict:
    """A Team node + its default Graph node (the registry lane's provisioning
    shape: ``/internal/provision`` mints the Team, ``_graph_create`` mints the
    Graph node — #2728 resolves the physical namespace from the Graph node)."""
    sdk = TortoiseSDK(db_path=db_path, namespace="registry")
    sdk._get_registry().query(
        "MERGE (t:Team {id:$id}) SET t.name=$id, t.tier='free'",
        params={"id": org_id})
    default = sdk._graph_create(org_id, "default", kind="default",
                                namespace=f"org_{org_id}")
    custom = sdk._graph_create(org_id, "custom", kind="custom")
    return {"default": default, "custom": custom}


def _auth(tc, org_id: str, **extra):
    """Override the management dependency with a resolved key_ctx (the
    key lane by default — a legacy full-access org-wide key)."""
    from tortoise.hosted_api import app, get_current_org_session
    ctx = {"org_id": org_id, "key_id": "k-1", "tier": "free",
           "scopes": [], "legacy_full_access": True, "graph_id": None}
    ctx.update(extra)
    app.dependency_overrides[get_current_org_session] = lambda: ctx


class TestGraphPacksRest:
    def test_get_unconfigured_graph_reports_configured_false(self, sel_client):
        tc, db_path = sel_client
        _seed_org(db_path, "t-sel-1")
        _auth(tc, "t-sel-1")
        r = tc.get("/v1/graphs/default/packs?org_id=t-sel-1")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["configured"] is False
        assert body["revision"] == 0

    def test_put_then_get_round_trips_and_version_stays_monotonic(
            self, sel_client):
        tc, db_path = sel_client
        _seed_org(db_path, "t-sel-2")
        _auth(tc, "t-sel-2")
        r = tc.put("/v1/graphs/default/packs?org_id=t-sel-2", json={
            "packs": [{"namespace": DEV}, {"namespace": MARKETING,
                                           "status": "disabled"}],
            "expected_revision": 0})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["configured"] is True and body["revision"] == 1
        assert {p["namespace"]: p["status"] for p in body["packs"]} == {
            DEV: "active", MARKETING: "disabled"}
        assert all(p["kind_count"] is not None for p in body["packs"])

        r2 = tc.get("/v1/graphs/default/packs?org_id=t-sel-2")
        assert r2.json()["revision"] == 1
        assert {p["namespace"] for p in r2.json()["packs"]} == {DEV, MARKETING}

    def test_put_revision_conflict_is_409_with_the_current_revision(
            self, sel_client):
        """FAIL-ON: a last-write-wins PUT would return 200 and silently erase
        the first admin's set."""
        tc, db_path = sel_client
        _seed_org(db_path, "t-sel-3")
        _auth(tc, "t-sel-3")
        first = tc.put("/v1/graphs/default/packs?org_id=t-sel-3", json={
            "packs": [{"namespace": DEV}], "expected_revision": 0})
        assert first.status_code == 200, first.text
        r = tc.put("/v1/graphs/default/packs?org_id=t-sel-3", json={
            "packs": [{"namespace": MARKETING}], "expected_revision": 0})
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["error_code"] == "PACK_SET_REVISION_CONFLICT"
        assert detail["current_revision"] == 1

    def test_expected_revision_is_required(self, sel_client):
        """Absent ``expected_revision`` is a 422 — the model refuses to let a
        caller opt out of concurrency by omission."""
        tc, db_path = sel_client
        _seed_org(db_path, "t-sel-4")
        _auth(tc, "t-sel-4")
        r = tc.put("/v1/graphs/default/packs?org_id=t-sel-4",
                   json={"packs": [{"namespace": DEV}]})
        assert r.status_code == 422, r.text

    def test_put_unknown_namespace_is_422(self, sel_client):
        tc, db_path = sel_client
        _seed_org(db_path, "t-sel-5")
        _auth(tc, "t-sel-5")
        r = tc.put("/v1/graphs/default/packs?org_id=t-sel-5", json={
            "packs": [{"namespace": "not-a-pack"}], "expected_revision": 0})
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["error_code"] == "UNKNOWN_PACK_NAMESPACE"

    def test_graph_bound_key_cannot_mutate(self, sel_client):
        """C5 #2114's boundary is preserved: pack selection is the management
        plane, so a graph-bound key is refused even with team:manage."""
        tc, db_path = sel_client
        _seed_org(db_path, "t-sel-6")
        _auth(tc, "t-sel-6", graph_id="g-bound",
              graph_namespace="org_t-sel-6_custom", scopes=["team:manage"])
        r = tc.put("/v1/graphs/default/packs?org_id=t-sel-6", json={
            "packs": [{"namespace": DEV}], "expected_revision": 0})
        assert r.status_code == 403, r.text
        assert r.json()["detail"]["error_code"] == "GRAPH_SCOPED_TEAM_SURFACE"

    def test_key_without_team_manage_cannot_mutate(self, sel_client):
        tc, db_path = sel_client
        _seed_org(db_path, "t-sel-7")
        _auth(tc, "t-sel-7", scopes=["graphs:write"],
              legacy_full_access=False)
        r = tc.put("/v1/graphs/default/packs?org_id=t-sel-7", json={
            "packs": [{"namespace": DEV}], "expected_revision": 0})
        assert r.status_code == 403, r.text

    def test_put_unknown_graph_is_404(self, sel_client):
        tc, db_path = sel_client
        _seed_org(db_path, "t-sel-8")
        _auth(tc, "t-sel-8")
        r = tc.put("/v1/graphs/g-does-not-exist/packs?org_id=t-sel-8", json={
            "packs": [{"namespace": DEV}], "expected_revision": 0})
        assert r.status_code == 404, r.text

    def test_get_unknown_org_is_404(self, sel_client):
        tc, db_path = sel_client
        _seed_org(db_path, "t-sel-9")
        _auth(tc, "t-sel-other")
        r = tc.get("/v1/graphs/default/packs?org_id=t-sel-9")
        assert r.status_code == 404, r.text

    def test_graph_bound_key_reads_only_its_own_graph(self, sel_client):
        """A graph-bound key's binding is a BOUNDARY, not a hint: reading a
        different graph's pack set would be a cross-graph leak (C5 #2114)."""
        tc, db_path = sel_client
        seeded = _seed_org(db_path, "t-sel-10")
        custom_ns = seeded["custom"]["namespace"]
        _auth(tc, "t-sel-10", graph_id=seeded["custom"]["graph_id"],
              graph_namespace=custom_ns)
        own = tc.get(
            f"/v1/graphs/{seeded['custom']['graph_id']}/packs?org_id=t-sel-10")
        assert own.status_code == 200, own.text
        # ... but the org's OTHER graph is refused.
        other = tc.get("/v1/graphs/default/packs?org_id=t-sel-10")
        assert other.status_code == 403, other.text
        assert other.json()["detail"]["error_code"] \
            == "GRAPH_SCOPED_KEY_CROSS_GRAPH"

    def test_graph_bound_key_with_unresolved_binding_fails_closed(
            self, sel_client):
        """`get_current_org` fail-closes a graph-bound key whose Graph node is
        missing to ``graph_namespace=None`` (C1 #2110). Such a key must be
        REFUSED, not treated as org-wide — a falsy binding is a refusal, never
        a pass.

        FAIL-ON: an implementation testing ``if bound and bound != namespace``
        would admit this request (the actual current-graph read succeeds
        instead of 403).
        """
        tc, db_path = sel_client
        _seed_org(db_path, "t-sel-13")
        _auth(tc, "t-sel-13", graph_id="g-ghost", graph_namespace=None)
        r = tc.get("/v1/graphs/default/packs?org_id=t-sel-13")
        assert r.status_code == 403, r.text

    def test_cross_graph_isolation_through_the_api(self, sel_client):
        """A change on one graph leaves the other graph's set untouched
        (indicator 3)."""
        tc, db_path = sel_client
        seeded = _seed_org(db_path, "t-sel-11")
        custom_gid = seeded["custom"]["graph_id"]
        _auth(tc, "t-sel-11")
        # Configure the CUSTOM graph first, then change the DEFAULT one.
        r0 = tc.put(f"/v1/graphs/{custom_gid}/packs?org_id=t-sel-11", json={
            "packs": [{"namespace": MARKETING}], "expected_revision": 0})
        assert r0.status_code == 200, r0.text
        r = tc.put("/v1/graphs/default/packs?org_id=t-sel-11", json={
            "packs": [{"namespace": DEV}], "expected_revision": 0})
        assert r.status_code == 200, r.text
        other = tc.get(
            f"/v1/graphs/{custom_gid}/packs?org_id=t-sel-11")
        assert other.status_code == 200, other.text
        assert other.json()["configured"] is True
        assert [p["namespace"] for p in other.json()["packs"]] == [MARKETING]

    def test_put_is_audited_once_per_change(self, sel_client, monkeypatch):
        """An audit record per change, with the key lane's ``via=api_key``
        convention so a key-driven write is never mistaken for a session."""
        calls: list[dict] = []
        import tortoise.hosted_api as ha

        async def _capture(request, org_id, operation, **kwargs):
            calls.append({"org_id": org_id, "operation": operation, **kwargs})

        monkeypatch.setattr(ha, "_async_audit", _capture)
        tc, db_path = sel_client
        _seed_org(db_path, "t-sel-12")
        _auth(tc, "t-sel-12", created_by=_U1)
        r = tc.put("/v1/graphs/default/packs?org_id=t-sel-12", json={
            "packs": [{"namespace": DEV}], "expected_revision": 0})
        assert r.status_code == 200, r.text
        assert len(calls) == 1, calls
        assert calls[0]["operation"] == "graph_packs_update"
        assert calls[0]["resource_id"] == "default"
        assert calls[0]["actor_user_id"] == _U1
        assert calls[0]["detail"]["via"] == "api_key"
        assert calls[0]["detail"]["revision"] == 1


def test_ps_public_helpers_are_importable():
    """The module exposes the state model the REST surface (and any future
    CLI/backfill) consumes — a rename is a breaking change, pinned here."""
    for name in ("resolved_pack_set", "set_graph_packs", "read_pack_selection",
                 "PackSelection", "ResolvedPackSet", "PackRevisionConflict"):
        assert hasattr(ps, name), name
