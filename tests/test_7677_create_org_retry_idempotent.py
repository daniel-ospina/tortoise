"""#7677 — the wait-bound refusal tells the caller to retry POST
/v1/organizations, but the retry was answered "Organization name already
exists" for the org that same caller had just created.

Mechanism under test (the SECOND attempt's view of the FIRST attempt's side
effect): ``WaitBoundMiddleware`` abandons but never cancels the handler
(``_track_wait_bound_request``), so an abandoned create can still commit the
org row + owner membership; the client's retry then re-enters ``create_org``
and its non-idempotent duplicate-name pre-check finds that row and 409s.

Fix direction (the ruling on #3834 constrains only the SIGNAL): make the
OPERATION survive being retried — an idempotent create when the name is
already held by the CALLER's OWN org — without weakening any refusal that
should still refuse. The transport bound, the route-exemption list and the
retry signal are NOT touched.

Falsifiers, both directions, each named individually:
- success: a same-owner repeat create returns the EXISTING org (200, same
  org_id, no second org row) — the pre-fix answer was 409;
- refusal: a different owner, an active NON-owner member, a non-active
  membership, a soft-deleted org, and a ``pending_payment`` org (not a created
  org) each still 409.
"""
from __future__ import annotations

import asyncio
import os
import tempfile

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from tests.fake_control_plane import FakeControlPlane
from tests.test_supabase_control import FREE_TEAM, _membership_row
from tortoise.hosted_api import app, get_current_user

_USER1 = "9f2c1a40-0000-4a00-8000-000000000001"
_USER2 = "9f2c1a40-0000-4a00-8000-000000000002"


# ── Supabase lane — direct, no graph work (every case stops at the gate) ────


@pytest.fixture
def fake() -> FakeControlPlane:
    return FakeControlPlane(
        {"organizations": [], "org_memberships": [], "api_keys": []})


def _seed_owned_org(fake, org_id: str, name: str, owner: str = _USER1, *,
                    role: str = "owner", status: str = "active",
                    subscription_status=None, deleted_at=None) -> None:
    row = dict(FREE_TEAM, id=org_id, name=name, graph_name=f"org_{org_id}")
    if subscription_status is not None:
        row["subscription_status"] = subscription_status
    if deleted_at is not None:
        row["deleted_at"] = deleted_at
    fake.seed("organizations", [row])
    fake.seed("org_memberships", [
        _membership_row(user_id=owner, org_id=org_id, role=role,
                        status=status)])


def _create_supabase(fake, name: str, user_id: str = _USER1):
    """Drive the Supabase create-org lane the way the route does."""
    from tortoise.hosted_api import _create_org_supabase_lane
    return asyncio.run(
        _create_org_supabase_lane(fake, name, {"user_id": user_id}))


class TestSupabaseLaneIdempotentRetry:
    def test_retry_by_the_owner_returns_the_existing_org(self, fake):
        """THE fix: the second attempt (the retry) succeeds and names the org
        the first attempt committed. Pre-fix this raised 409."""
        _seed_owned_org(fake, "t-dup", "Acme")
        out = _create_supabase(fake, "Acme")
        assert out["org_id"] == "t-dup"
        assert out["graph_name"] == "org_t-dup"
        assert out["tier"] == "free"
        assert out["name"] == "Acme"

    def test_retry_mints_no_second_org(self, fake):
        _seed_owned_org(fake, "t-dup", "Acme")
        before = len(fake.query("organizations"))
        _create_supabase(fake, "Acme")
        assert len(fake.query("organizations")) == before

    def test_retry_carries_the_org_id_so_the_caller_can_reach_it(self, fake):
        """The whole point of the issue: the 504 body swallowed the create
        response, so the org_id is only obtainable from the retry."""
        _seed_owned_org(fake, "t-own", "Acme")
        out = _create_supabase(fake, "Acme")
        assert out["org_id"] == "t-own"

    # ── every refusal that must still refuse, named individually ───────────

    def test_different_owner_still_refused(self, fake):
        """A name held by ANOTHER user is genuinely taken — 409."""
        _seed_owned_org(fake, "t-other", "Acme", owner=_USER2)
        with pytest.raises(HTTPException) as exc:
            _create_supabase(fake, "Acme", user_id=_USER1)
        assert exc.value.status_code == 409
        assert exc.value.detail == "Organization name already exists"

    def test_active_non_owner_member_still_refused(self, fake):
        """Being a MEMBER of someone else's org conveys no ownership — a
        collaborator must not be handed the org as their own create."""
        _seed_owned_org(fake, "t-other", "Acme", owner=_USER1, role="member")
        with pytest.raises(HTTPException) as exc:
            _create_supabase(fake, "Acme")
        assert exc.value.status_code == 409

    def test_non_active_owner_membership_still_refused(self, fake):
        """A removed membership is not ownership either."""
        _seed_owned_org(fake, "t-past", "Acme", owner=_USER1, status="removed")
        with pytest.raises(HTTPException) as exc:
            _create_supabase(fake, "Acme")
        assert exc.value.status_code == 409

    def test_soft_deleted_org_still_refused(self, fake):
        """A soft-deleted org's name is still taken and the org is NOT a
        successfully created org — the retry must not resurrect it."""
        _seed_owned_org(fake, "t-gone", "Acme", owner=_USER1,
                        deleted_at="2026-01-01T00:00:00+00:00")
        with pytest.raises(HTTPException) as exc:
            _create_supabase(fake, "Acme")
        assert exc.value.status_code == 409

    def test_pending_payment_org_still_refused(self, fake):
        """A ``pending_payment`` org is not a real/provisioned org (#2789) and
        the create lane never produces one — it is not "the same create", so
        the retry must not surface it as a successful create."""
        _seed_owned_org(fake, "t-pending", "Acme", owner=_USER1,
                        subscription_status="pending_payment")
        with pytest.raises(HTTPException) as exc:
            _create_supabase(fake, "Acme")
        assert exc.value.status_code == 409

    def test_valid_name_with_no_existing_org_still_creates(self, fake):
        """The replay branch must not swallow the fresh-create path."""
        out = _create_supabase(fake, "Acme")
        assert out["name"] == "Acme"
        assert len(fake.query("organizations")) == 1

    def test_rate_limited_retry_still_429_before_the_replay(self, fake):
        """The gate order is pinned 429 -> 409 -> 402, and the replay lives
        at the duplicate-name (409) gate — so a caller at the org-create rate
        limit is still answered 429 for their own org name until the window
        passes. That 429 is itself honest and retryable (it does not assert a
        false "already exists"), and the replay is reached afterwards."""
        from datetime import UTC, datetime, timedelta
        _seed_owned_org(fake, "t-dup", "Acme")
        since = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
        for i in range(3):
            fake.seed("org_memberships", [
                _membership_row(user_id=_USER1, org_id=f"team-{i}",
                                role="owner", created_at=since)])
        with pytest.raises(HTTPException) as exc:
            _create_supabase(fake, "Acme")
        assert exc.value.status_code == 429


# ── Supabase lane — through the real route (the sequence that 504s) ─────────


@pytest.fixture
def supabase_fake(monkeypatch) -> FakeControlPlane:
    import tortoise.supabase_control as sc
    fake = FakeControlPlane(
        {"organizations": [], "org_memberships": [], "api_keys": []})
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_role_key_test")
    monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
    return fake


def _close_keepalive_anchors(module) -> None:
    for ns in list(module._FALLBACK_KEEPALIVE):
        anchor = module._FALLBACK_KEEPALIVE.pop(ns, None)
        if anchor is not None:
            try:  # noqa: SIM105
                anchor.close()
            except Exception:
                pass


@pytest.fixture
def supabase_client(monkeypatch, supabase_fake):
    import tortoise.hosted_api as ha_mod
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "retry7677.db")
        _orig = ha_mod.TortoiseSDK.__init__
        ha_mod._FALLBACK_KEEPALIVE.clear()

        def _patched(self, db_path_arg=None, *, namespace=None, **kwargs):
            _orig(self, db_path, namespace=namespace)

        ha_mod.TortoiseSDK.__init__ = _patched
        os.environ["TORTOISE_DB_PATH"] = db_path
        try:
            with TestClient(app) as tc:
                app.dependency_overrides[get_current_user] = lambda: {
                    "user_id": _USER1, "email": "owner@example.com"}
                yield tc
        finally:
            os.environ.pop("TORTOISE_DB_PATH", None)
            ha_mod.TortoiseSDK.__init__ = _orig
            _close_keepalive_anchors(ha_mod)
            app.dependency_overrides.clear()


class TestSupabaseRouteRetry:
    def test_first_create_commits_and_the_retry_reaches_the_same_org(
            self, supabase_client, supabase_fake):
        """The full sequence at the level the 504 leaves it: attempt 1 commits
        (simulated here by an acknowledged 200 — the abandoned-but-running
        handler's side effect is the same row), attempt 2 (the retry) must be
        answered with THAT org, not 409."""
        r1 = supabase_client.post("/v1/organizations", json={"name": "Acme"})
        assert r1.status_code == 200, r1.text
        org_id = r1.json()["org_id"]

        # the retry observes attempt 1's committed side effect
        r2 = supabase_client.post("/v1/organizations", json={"name": "Acme"})
        assert r2.status_code == 200, r2.text
        assert r2.json()["org_id"] == org_id
        assert len(supabase_fake.query("organizations")) == 1

    def test_another_users_retry_is_still_a_409(
            self, supabase_client, supabase_fake):
        r1 = supabase_client.post("/v1/organizations", json={"name": "Acme"})
        assert r1.status_code == 200, r1.text

        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _USER2, "email": "other@example.com"}
        r2 = supabase_client.post("/v1/organizations", json={"name": "Acme"})
        assert r2.status_code == 409, r2.text
        assert "already exists" in r2.json()["detail"]


# ── Registry (selfhost) lane — the identical shape ─────────────────────────


@pytest.fixture
def registry_client(monkeypatch):
    """TestClient in REGISTRY mode on a temp embedded DB (mirrors
    tests/test_free_team_entitlement.py)."""
    import tortoise.hosted_api as ha_mod
    for var in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
                "SUPABASE_SERVICE_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "registry-7677.db")
        _orig = ha_mod.TortoiseSDK.__init__
        ha_mod._FALLBACK_KEEPALIVE.clear()

        def _patched(self, db_path_arg=None, *, namespace=None, **kwargs):
            _orig(self, db_path, namespace=namespace)

        ha_mod.TortoiseSDK.__init__ = _patched
        os.environ["TORTOISE_DB_PATH"] = db_path
        try:
            with TestClient(app) as tc:
                app.dependency_overrides[get_current_user] = lambda: {
                    "user_id": _USER1, "email": "owner@example.com"}
                yield tc
        finally:
            os.environ.pop("TORTOISE_DB_PATH", None)
            ha_mod.TortoiseSDK.__init__ = _orig
            _close_keepalive_anchors(ha_mod)
            app.dependency_overrides.clear()


def _registry():
    from tortoise.hosted_api import _make_sdk
    return _make_sdk(namespace="registry")._get_registry()


def _as_user(tc, user_id: str, email: str) -> None:
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": user_id, "email": email}


class TestRegistryLaneIdempotentRetry:
    def test_retry_by_the_owner_returns_the_existing_org(self, registry_client):
        r1 = registry_client.post("/v1/organizations", json={"name": "Acme"})
        assert r1.status_code == 200, r1.text
        org_id = r1.json()["org_id"]

        r2 = registry_client.post("/v1/organizations", json={"name": "Acme"})
        assert r2.status_code == 200, r2.text
        assert r2.json()["org_id"] == org_id
        rows = _registry().query(
            "MATCH (t:Team {name:$n}) RETURN count(t)",
            params={"n": "Acme"}).result_set
        assert rows[0][0] == 1

    def test_different_owner_still_refused(self, registry_client):
        r1 = registry_client.post("/v1/organizations", json={"name": "Acme"})
        assert r1.status_code == 200, r1.text
        _as_user(registry_client, _USER2, "other@example.com")
        r2 = registry_client.post("/v1/organizations", json={"name": "Acme"})
        assert r2.status_code == 409, r2.text
        assert "already exists" in r2.json()["detail"]

    def test_active_non_owner_member_still_refused(self, registry_client):
        r1 = registry_client.post("/v1/organizations", json={"name": "Acme"})
        assert r1.status_code == 200, r1.text
        org_id = r1.json()["org_id"]
        # #7677: seeded directly — the free tier's max_users=1 would refuse a
        # second membership through membership_create.
        _registry().query(
            "MATCH (t:Team {id:$i}) "
            "CREATE (m:Membership {id:'m-x', user_id:$u, org_id:$i, "
            "role:'member', status:'active'})",
            params={"i": org_id, "u": _USER2})
        _as_user(registry_client, _USER2, "other@example.com")
        r2 = registry_client.post("/v1/organizations", json={"name": "Acme"})
        assert r2.status_code == 409, r2.text

    def test_non_active_owner_membership_still_refused(self, registry_client):
        r1 = registry_client.post("/v1/organizations", json={"name": "Acme"})
        assert r1.status_code == 200, r1.text
        org_id = r1.json()["org_id"]
        _registry().query(
            "MATCH (m:Membership {org_id:$i, user_id:$u, role:'owner'}) "
            "SET m.status = 'removed'",
            params={"i": org_id, "u": _USER1})
        r2 = registry_client.post("/v1/organizations", json={"name": "Acme"})
        assert r2.status_code == 409, r2.text

    def test_soft_deleted_org_still_refused(self, registry_client):
        """The replay requires a LIVE org. The real soft-delete cascade also
        removes memberships, but this test leaves the owner membership ACTIVE
        on purpose — proving the explicit ``deleted_at`` guard, not the
        cascade ordering, is what refuses."""
        r1 = registry_client.post("/v1/organizations", json={"name": "Acme"})
        assert r1.status_code == 200, r1.text
        org_id = r1.json()["org_id"]
        _registry().query(
            "MATCH (t:Team {id:$i}) SET t.deleted_at = $now",
            params={"i": org_id, "now": "2026-01-01T00:00:00+00:00"})
        r2 = registry_client.post("/v1/organizations", json={"name": "Acme"})
        assert r2.status_code == 409, r2.text

    def test_two_teams_with_the_same_name_still_refused(self, registry_client):
        """The replay requires EXACTLY one Team match — an ambiguous registry
        (two Teams sharing a name) must never be resolved by guesswork."""
        reg = _registry()
        reg.query(
            "CREATE (t:Team {id:'dup-a', name:'Acme', "
            "graph_name:'org_dup_a', tier:'free'})")
        reg.query(
            "CREATE (t:Team {id:'dup-b', name:'Acme', "
            "graph_name:'org_dup_b', tier:'free'})")
        reg.query(
            "MATCH (t:Team {id:'dup-a'}) "
            "CREATE (m:Membership {id:'m-a', user_id:$u, org_id:'dup-a', "
            "role:'owner', status:'active'})",
            params={"u": _USER1})
        r = registry_client.post("/v1/organizations", json={"name": "Acme"})
        assert r.status_code == 409, r.text
