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


def _sdk(tmp_path, namespace: str, *, db: str | None = None) -> TortoiseSDK:
    return TortoiseSDK(db_path=str(tmp_path / f"{db or namespace}.db"),
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

    def test_include_removed_is_the_one_reader_that_sees_tombstones(
            self, tmp_path):
        """``include_removed=True`` is the Configured-packs add-list's read.

        The user surface must be able to add a removed pack BACK: removal is a
        desired-state OMIT, so without this arm a removed namespace is in
        neither the introspection list (tombstones omitted) nor — for a
        tenant-authored pack — the shared catalog. FAIL-ON: the default call
        still omits it (that contract is asserted directly above), so an
        implementation that widened the default would red the sibling test.
        """
        sdk = _sdk(tmp_path, "test_sel_include_removed")
        set_graph_packs(sdk, packs=[{"namespace": DEV}, {"namespace": MARKETING}],
                        expected_revision=0)
        set_graph_packs(sdk, packs=[{"namespace": DEV}], expected_revision=1)
        rows = {p["namespace"]: p["status"]
                for p in get_tenant_packs(sdk, include_removed=True)}
        assert rows == {DEV: "active", MARKETING: "removed"}

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

    def test_lost_first_write_cas_refuses_and_writes_nothing(
            self, tmp_path, monkeypatch):
        """The FIRST configuration is a compare-and-swap too (#8648 review P2).

        ``test_lost_cas_race_refuses_and_writes_nothing`` monkeypatches
        ``_bump_selection`` and always runs AFTER a successful first write, so
        it can never reach the first-write branch — the unconditional
        ``MERGE ... SET revision = 1`` there was invisible to it. This test
        simulates the cross-worker race on a graph's FIRST PUT: the pre-read
        sees no marker while a peer has already created one.

        FAIL-ON: pre-fix the first-write branch is unconditional, so this call
        does not conflict — it tombstones ``dev``, activates ``marketing`` and
        renumbers the peer's advanced marker BACKWARDS to 1.
        """
        sdk = _sdk(tmp_path, "test_sel_first_cas")
        set_graph_packs(sdk, packs=[{"namespace": DEV}], expected_revision=0)
        real_read_selection = ps._read_selection
        calls = {"n": 0}

        def _stale_once(g):
            calls["n"] += 1
            if calls["n"] == 1:
                return None  # the pre-read that lost the race
            return real_read_selection(g)

        monkeypatch.setattr(ps, "_read_selection", _stale_once)
        with pytest.raises(PackRevisionConflict) as exc:
            set_graph_packs(sdk, packs=[{"namespace": MARKETING}],
                            expected_revision=0)
        assert exc.value.current == 1
        monkeypatch.setattr(ps, "_read_selection", real_read_selection)
        assert read_pack_selection(sdk).revision == 1
        assert dict(_raw_installs(sdk)) == {DEV: "active"}
        assert resolved_pack_set(sdk).namespaces == frozenset({DEV})

    def test_backfill_activation_does_not_reseed_a_configured_graph(
            self, tmp_path):
        """The marker must suppress the OTHER seeding path too (#8648 P2).

        ``get_tenant_packs``'s self-heal honours the marker, but
        ``ensure_tenant_packs`` — the path a provision retry and
        ``graph-scripts/backfill_pack_installs.py`` (documented "safe to
        re-run") call — used to unconditionally MERGE every starter namespace
        and set ``status='active'``, resurrecting removed/disabled packs with
        the revision unchanged.

        FAIL-ON: pre-fix ``activated`` lists every starter namespace and the
        resolved set widens back to the starter set.
        """
        sdk = _sdk(tmp_path, "test_sel_backfill")
        set_graph_packs(sdk, packs=[{"namespace": DEV}], expected_revision=0)
        activated = ensure_tenant_packs(sdk)
        assert activated == []
        assert read_pack_selection(sdk).revision == 1
        assert resolved_pack_set(sdk).namespaces == frozenset({DEV})
        assert [p["namespace"] for p in get_tenant_packs(sdk)] == [DEV]

    def test_backfill_activation_skips_when_marker_read_fails(
            self, tmp_path, monkeypatch):
        """An unreadable marker must NOT fall through to seeding.

        ``ensure_tenant_packs`` is best-effort and must never raise into the
        provisioning path — but when the marker read fails we cannot prove the
        graph is unconfigured, so the safe direction is to skip rather than
        widen a deliberately configured graph (narrowing, never widening).

        FAIL-ON: pre-fix ``ensure_tenant_packs`` never reads the marker, so it
        seeds the whole starter set and returns five records.
        """
        sdk = _sdk(tmp_path, "test_sel_read_fail")
        real = ps._read_selection

        def _boom(_g):
            raise RuntimeError("simulated marker read failure")

        monkeypatch.setattr(ps, "_read_selection", _boom)
        assert ensure_tenant_packs(sdk) == []
        monkeypatch.setattr(ps, "_read_selection", real)
        assert _raw_installs(sdk) == []

    def test_changes_are_isolated_to_their_graph(self, tmp_path):
        """A change on graph A must leave graph B untouched.

        Both graphs live in ONE embedded database (#8648 review P2). The
        original fixture put them in two separate ``.db`` FILES, which share no
        graph at all — the assertion was satisfied by construction and no
        cross-graph leak was observable. The two preconditions below pin the
        discriminating setup: ONE db, TWO distinct physical graphs.

        FAIL-ON: graph B is seeded with ``marketing`` in the SAME db; a leak —
        a resolver or writer that ignored the scoped graph — would show B
        losing the namespace when A is narrowed, which the two-DB fixture
        could never surface.
        """
        a = _sdk(tmp_path, "test_sel_a", db="test_sel_shared")
        b = _sdk(tmp_path, "test_sel_b", db="test_sel_shared")
        # The setup must be able to discriminate (same store, distinct graphs).
        assert a._db_path == b._db_path
        assert ps._resolved_graph_name(a, None) != ps._resolved_graph_name(b, None)
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


def _seed_member(db_path: str, org_id: str, user_id: str, role: str) -> None:
    """An active ``Membership`` node in the registry graph, so the session
    lane of the dual-lane authorization resolves (#2728)."""
    sdk = TortoiseSDK(db_path=db_path, namespace="registry")
    sdk._get_registry().query(
        "MERGE (m:Membership {user_id:$u, org_id:$t}) "
        "SET m.status='active', m.role=$r",
        params={"u": user_id, "t": org_id, "r": role})


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

    def test_get_offers_catalog_packs_the_graph_does_not_have(
            self, sel_client):
        """The Configured-packs view must be able to ADD a pack, which means
        the read has to name what is addable — the graph's own records cannot
        answer that.

        FAIL-ON: a payload without ``available`` (or one that echoed the
        graph's own set back) leaves the modal with no source for an add-list,
        so "add" would either not exist or offer a namespace already present.
        ``venture`` is asserted specifically: it ships in the catalog and is
        NOT a starter, so a starter-derived add-list would miss it — the one
        pack a per-graph approval surface most needs to be able to offer.
        """
        tc, db_path = sel_client
        _seed_org(db_path, "t-sel-avail")
        _auth(tc, "t-sel-avail")
        r = tc.put("/v1/graphs/default/packs?org_id=t-sel-avail", json={
            "packs": [{"namespace": DEV}], "expected_revision": 0})
        assert r.status_code == 200, r.text
        body = r.json()
        offered = {p["namespace"] for p in body["available"]}
        assert DEV not in offered, "a listed pack must never be offered twice"
        assert MARKETING in offered
        assert "venture" in offered, (
            "a catalog pack that is not a starter must still be addable")
        by_ns = {p["namespace"]: p for p in body["available"]}
        assert by_ns[MARKETING]["status"] == "available"
        assert by_ns[MARKETING]["kind_count"] is not None

    def test_a_removed_pack_is_offered_back_with_its_status(
            self, sel_client):
        """Removal must not be a one-way door in the UI: the removed
        namespace has to come back in the add-list, marked as previously
        removed, and re-adding it must restore it.

        FAIL-ON: an add-list built from the catalog ALONE would hide a
        tenant-authored tombstone entirely (no catalog entry exists for it).
        """
        tc, db_path = sel_client
        _seed_org(db_path, "t-sel-readd")
        _auth(tc, "t-sel-readd")
        first = tc.put("/v1/graphs/default/packs?org_id=t-sel-readd", json={
            "packs": [{"namespace": DEV}, {"namespace": MARKETING}],
            "expected_revision": 0})
        assert first.status_code == 200, first.text
        removed = tc.put("/v1/graphs/default/packs?org_id=t-sel-readd", json={
            "packs": [{"namespace": DEV}], "expected_revision": 1})
        assert removed.status_code == 200, removed.text
        body = removed.json()
        assert [p["namespace"] for p in body["packs"]] == [DEV]
        offered = {p["namespace"]: p for p in body["available"]}
        # MARKETING is in the shared catalog, so the catalog arm offers it as
        # a fresh 'available' — either shape is addable; what must hold is
        # that it is OFFERED and not duplicated.
        assert MARKETING in offered
        assert offered[MARKETING]["status"] in ("available", "removed")
        assert sum(1 for p in body["available"]
                   if p["namespace"] == MARKETING) == 1, (
            "a namespace must appear at most once in the add-list")
        back = tc.put("/v1/graphs/default/packs?org_id=t-sel-readd", json={
            "packs": [{"namespace": DEV}, {"namespace": MARKETING}],
            "expected_revision": 2})
        assert back.status_code == 200, back.text
        assert {p["namespace"]: p["status"]
                for p in back.json()["packs"]}[MARKETING] == "active"

    def test_a_tenant_authored_tombstone_is_offered_back(self, sel_client,
                                                         monkeypatch):
        """The add-list's TOMBSTONE arm — the reason ``available`` exists.

        ``available`` is justified by the tenant-authored case ("removal would
        have been a one-way door — a tenant-authored pack appears in no shared
        catalog"), which is the ``for row in sorted(removed, ...)`` arm of
        ``_packs_payload``. The sibling test above cannot reach it: it removes
        MARKETING, which IS in the shared catalog, so the CATALOG arm offers it
        and the tombstone arm ``continue``s — the two shapes are
        interchangeable there. Reachable in production: a tenant-manifest
        install (``POST /v1/packs/manifests``) mints ``:PackInstall`` records
        for namespaces outside the catalog, and ``set_graph_packs`` permits
        re-adding them because ``existing`` still carries the tombstone.

        FAIL-ON: drop the tombstone arm (back to "catalog only") and the
        namespace disappears from ``available`` entirely — a removed
        tenant-authored pack could never be added back, which is the one-way
        door this surface exists to open. Every other test in the file stays
        green without it, which is why it is pinned here.
        """
        tc, db_path = sel_client
        org = "t-sel-tenant-pack"
        tenant = "acme-ops"
        _seed_org(db_path, org)
        _auth(tc, org)
        # The state: a namespace the WRITE side knows (as a manifest install
        # makes it — the record is what permits re-adding) but that NO shipped
        # pack carries. Only ``ps._resolve_catalog`` is widened; the VIEW's
        # catalog (``hosted_api._pack_catalog_rows``, read off the domain
        # registry) never sees it, so the catalog arm cannot offer it.
        real_catalog = ps._resolve_catalog
        monkeypatch.setattr(
            ps, "_resolve_catalog",
            lambda: dict(real_catalog(), **{tenant: {"namespace": tenant}}))
        first = tc.put(f"/v1/graphs/default/packs?org_id={org}", json={
            "packs": [{"namespace": tenant}], "expected_revision": 0})
        assert first.status_code == 200, first.text
        assert [p["namespace"] for p in first.json()["packs"]] == [tenant]

        removed = tc.put(f"/v1/graphs/default/packs?org_id={org}", json={
            "packs": [], "expected_revision": 1})
        assert removed.status_code == 200, removed.text
        body = removed.json()
        assert body["packs"] == [], "a removed pack leaves the visible set"
        offered = [p for p in body["available"] if p["namespace"] == tenant]
        assert len(offered) == 1, (
            "the tenant-authored tombstone must be offered back exactly once "
            f"(available={[p['namespace'] for p in body['available']]})")
        assert offered[0]["status"] == "removed", (
            "the row must say it WAS removed — 'available' would pretend the "
            "namespace is new to this graph")

        # …and the door really opens both ways: re-adding restores it.
        back = tc.put(f"/v1/graphs/default/packs?org_id={org}", json={
            "packs": [{"namespace": tenant}], "expected_revision": 2})
        assert back.status_code == 200, back.text
        assert {p["namespace"]: p["status"]
                for p in back.json()["packs"]}[tenant] == "active"

    def test_available_rows_carry_the_source_the_save_will_write(
            self, sel_client):
        """The Source column must be the value the WRITE will store, not a
        client-side guess.

        ``available`` rows are catalog rows and carried no ``source`` at all,
        so ``packs.js``'s ``toRow`` invented one — and labelled a
        shipped-but-not-starter pack "Starter" while claiming "Manual" in the
        same row object, flipping to "Manual" only after the save wrote the
        install record. ``set_graph_packs`` writes ``"starter" if ns in
        starter else "manual"``, so the read now says the same thing.

        FAIL-ON: without ``source`` on the catalog rows (and without
        ``catalog_ok``) both assertions raise KeyError.
        """
        tc, db_path = sel_client
        _seed_org(db_path, "t-sel-source")
        _auth(tc, "t-sel-source")
        r = tc.put("/v1/graphs/default/packs?org_id=t-sel-source", json={
            "packs": [{"namespace": DEV}], "expected_revision": 0})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["catalog_ok"] is True, (
            "a readable catalog must be reported as readable, or the client "
            "cannot tell an empty add-list from an unread one")
        by_ns = {p["namespace"]: p for p in body["available"]}
        assert by_ns[MARKETING]["source"] == "starter", (
            "a starter namespace's row must say starter — the same value the "
            "install record will carry")
        assert by_ns["venture"]["source"] == "manual", (
            "venture ships in the catalog but is NOT a starter, so its row "
            "must not claim to be one")

    def test_a_degraded_catalog_is_reported_not_papered_over(
            self, sel_client, monkeypatch):
        """An unreadable catalog must be VISIBLE as such.

        ``_pack_catalog_rows`` degrades to an empty dict rather than 503 a
        read that already has the graph's own set in hand. The add-list is
        then empty for the opposite reason it is normally empty, and the
        client rendered that as the confident claim "Every pack in the
        catalog is already on this graph." ``catalog_ok`` is the status bit
        that lets it say "the catalog could not be read" instead.

        FAIL-ON: without ``catalog_ok`` the assertion raises KeyError; with a
        flag that is always True a degraded read would be indistinguishable
        from a complete one.
        """
        import tortoise.hosted_api as ha
        tc, db_path = sel_client
        _seed_org(db_path, "t-sel-catalog-down")
        _auth(tc, "t-sel-catalog-down")
        monkeypatch.setattr(ha, "_pack_catalog_rows", lambda: ({}, False))
        r = tc.get("/v1/graphs/default/packs?org_id=t-sel-catalog-down")
        assert r.status_code == 200, (
            "degrading is the point — the graph's own set is still readable")
        body = r.json()
        assert body["catalog_ok"] is False
        assert body["available"] == [], (
            "the add-list is empty precisely BECAUSE the catalog was unread — "
            "the bit, not the emptiness, is what the client must render")

    def test_an_unavailable_registry_is_not_reported_as_a_readable_catalog(
            self, monkeypatch):
        """``catalog_ok`` must be FALSE when the pack registry is UNAVAILABLE.

        ``_get_registry()`` RETURNS ``None`` on its documented degradation path
        (``domain_loader``: "None if packs are unavailable") — it does not
        raise. Dereferencing that with ``getattr(reg, "packs", None) or {}``
        yields an EMPTY dict with no exception, so the reader reported
        ``catalog_ok=True`` over a catalog it never read: the modal then
        rendered "Every pack in the catalog is already on this graph" (the
        confident claim about an unread catalog) and withheld the Reload that
        only the degraded copy carries. Both sibling readers of the same
        function guard the None (``pack_state._resolve_catalog``,
        ``domain_loader._pack_kinds_by_bucket``).

        FAIL-ON: pre-fix this returns ``({}, True)``. ``None`` — not an
        exception — is the failure mode the ``except`` cannot catch, and no
        other test reaches it (the sibling degradation test monkeypatches
        ``_pack_catalog_rows`` itself, so it pins the plumbing of the bit and
        never the classification inside it).
        """
        import tortoise.domain_loader as dl
        import tortoise.hosted_api as ha

        monkeypatch.setattr(dl, "_get_registry", lambda: None)
        assert ha._pack_catalog_rows() == ({}, False), (
            "an unavailable registry is not a readable catalog")

    def test_a_tombstoned_record_keeps_its_recorded_source(
            self, sel_client, monkeypatch):
        """The add-list's ``source`` must be the value the SAVE will store.

        ``set_graph_packs`` writes ``rec.get("source") or ("starter" if ns in
        starter else "manual")`` — an EXISTING record's source always wins. The
        catalog arm derived from the CURRENT starter set instead, so a
        namespace whose record disagreed with that derivation flipped its
        Source column under the user the moment they added it back (the
        "column flipped after a Save" defect the Source column exists to
        prevent).

        The disagreement is reachable: a namespace seeded as ``starter`` and
        later dropped from ``TORTOISE_STARTER_PACKS`` keeps ``source='starter'``
        on its record (the additive-only-removal case ``pack_state``'s module
        docstring contemplates), while the derivation now says ``manual``.

        FAIL-ON: pre-fix the offer carries the derivation (``manual`` under the
        narrowed starter set) while the record — and therefore the re-add —
        says ``starter``.
        """
        tc, db_path = sel_client
        org = "t-sel-source-record"
        _seed_org(db_path, org)
        _auth(tc, org)
        # MARKETING is a starter, so the write mints its record with
        # source='starter' (the starter set is still the default here).
        first = tc.put(f"/v1/graphs/default/packs?org_id={org}", json={
            "packs": [{"namespace": MARKETING}], "expected_revision": 0})
        assert first.status_code == 200, first.text
        # …and now the DERIVATION disagrees with that record.
        monkeypatch.setattr(ps, "_starter_namespaces", lambda: [])
        removed = tc.put(f"/v1/graphs/default/packs?org_id={org}", json={
            "packs": [], "expected_revision": 1})
        assert removed.status_code == 200, removed.text
        body = removed.json()
        offered = [p for p in body["available"] if p["namespace"] == MARKETING]
        assert len(offered) == 1, (
            "MARKETING is in the shared catalog, so the catalog arm offers it "
            "exactly once")
        assert offered[0]["source"] == "starter", (
            "the offer must carry the RECORD's source — the value the re-add "
            "will store — not a fresh starter-set derivation that says manual")

    def test_a_null_source_record_reads_the_value_the_save_will_store(
            self, sel_client):
        """#2728 review P2-2: the READ's ``source`` derivation is the WRITE's.

        ``get_tenant_packs`` defaulted a NULL ``source`` to ``starter``
        unconditionally, while ``set_graph_packs`` derives
        ``rec.get("source") or ("starter" if ns in starter else "manual")``.
        A record with NO source is produced by the live import path
        (``_apply_import_pack_config`` MERGEs version/status/installed_at and
        never ``source``), so restoring a dump that names a catalog NON-starter
        showed **"Starter"** on the row and — after any edit + Save wrote
        ``source='manual'`` — became **"Manual"**: the "column flipped after a
        Save" defect the Source column exists to prevent, and the contract the
        ``_packs_payload`` docstring and ``docs/EXPANSION_PACKS.md`` state.

        FAIL-ON: pre-fix the row reads ``starter`` for ``venture`` (a catalog
        non-starter), i.e. a different value from the one the Save below
        stores — the two assertions disagree with each other, which is the
        whole defect. The record is seeded raw because no API path mints a
        sourceless install (only a restored pre-``source`` dump does).
        """
        tc, db_path = sel_client
        org = "t-sel-null-source"
        _seed_org(db_path, org)
        _auth(tc, org)
        # ``org_<org_id>`` is the physical graph the route resolves;
        # ``_make_sdk(graph_name=…)`` is the EXACT construction
        # ``_graph_packs_view_for`` reads it through, so the seed cannot land on
        # a sibling graph (a `namespace=f"org_{org}"` SDK would prepend `org_`
        # a second time).
        import tortoise.hosted_api as ha
        tenant = ha._make_sdk(graph_name=f"org_{org}")
        tenant._get_proj().g.query(
            "MERGE (p:PackInstall {namespace: $ns}) "
            "SET p.version = '0.1.0', p.status = 'active'",
            params={"ns": "venture"})
        read = tc.get(f"/v1/graphs/default/packs?org_id={org}")
        assert read.status_code == 200, read.text
        rows = {p["namespace"]: p for p in read.json()["packs"]}
        assert "venture" in rows, (
            "the seeded install must be the record the route reads "
            f"(got {sorted(rows)})")
        read_source = rows["venture"]["source"]
        assert read_source == "manual", (
            "a sourceless record for a NON-starter namespace must read as "
            "manual — the value set_graph_packs writes for it — not a "
            "starter default that flips on the next Save")
        # …and the proof that "manual" is the value the write stores: any edit
        # (here a no-op re-save of the same set) leaves the column unchanged.
        saved = tc.put(f"/v1/graphs/default/packs?org_id={org}", json={
            "packs": [{"namespace": "venture"}], "expected_revision": 0})
        assert saved.status_code == 200, saved.text
        saved_source = {p["namespace"]: p
                        for p in saved.json()["packs"]}["venture"]["source"]
        assert saved_source == read_source, (
            "the read and the write must agree on source, or the Source "
            "column flips under the user on Save")

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


class TestGraphPacksSessionLane:
    """The SESSION lane of the new authorization (#8648 review P2).

    Every other REST test's ``_auth()`` injects ``key_id="k-1"``, so
    ``key_ctx["key_id"]`` is always truthy and the owner/admin membership
    branch in ``_apply_graph_packs_put`` — the check deciding who may rewrite
    a graph's approved ontology — never ran, nor did the member check in
    ``_graph_packs_read``. These tests make that branch live: dropping
    ``or membership.get("role") not in ("owner", "admin")`` now FAILS
    ``test_session_plain_member_cannot_mutate``.
    """

    def test_session_member_may_read(self, sel_client):
        tc, db_path = sel_client
        _seed_org(db_path, "t-sel-s1")
        _seed_member(db_path, "t-sel-s1", "member-1", "member")
        _auth(tc, "t-sel-s1", key_id=None, session_user_id="member-1")
        r = tc.get("/v1/graphs/default/packs?org_id=t-sel-s1")
        assert r.status_code == 200, r.text
        assert r.json()["graph_id"] == "default"

    def test_session_non_member_read_is_403(self, sel_client):
        tc, db_path = sel_client
        _seed_org(db_path, "t-sel-s2")
        _auth(tc, "t-sel-s2", key_id=None, session_user_id="outsider")
        r = tc.get("/v1/graphs/default/packs?org_id=t-sel-s2")
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == "No membership in team"

    def test_session_owner_may_mutate(self, sel_client):
        tc, db_path = sel_client
        _seed_org(db_path, "t-sel-s3")
        _seed_member(db_path, "t-sel-s3", "owner-1", "owner")
        _auth(tc, "t-sel-s3", key_id=None, session_user_id="owner-1")
        r = tc.put("/v1/graphs/default/packs?org_id=t-sel-s3", json={
            "packs": [{"namespace": DEV}], "expected_revision": 0})
        assert r.status_code == 200, r.text
        assert r.json()["revision"] == 1

    def test_session_plain_member_cannot_mutate(self, sel_client):
        tc, db_path = sel_client
        _seed_org(db_path, "t-sel-s4")
        _seed_member(db_path, "t-sel-s4", "member-2", "member")
        _auth(tc, "t-sel-s4", key_id=None, session_user_id="member-2")
        r = tc.put("/v1/graphs/default/packs?org_id=t-sel-s4", json={
            "packs": [{"namespace": DEV}], "expected_revision": 0})
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == "Requires owner or admin role in team"


def test_ps_public_helpers_are_importable():
    """The module exposes the state model the REST surface (and any future
    CLI/backfill) consumes — a rename is a breaking change, pinned here."""
    for name in ("resolved_pack_set", "set_graph_packs", "read_pack_selection",
                 "PackSelection", "ResolvedPackSet", "PackRevisionConflict"):
        assert hasattr(ps, name), name
