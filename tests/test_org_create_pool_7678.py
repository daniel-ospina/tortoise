"""#7678 — the org-create control-plane path: a dedicated pool + one membership read.

Two properties, each with a falsifier that goes red on revert:

* **Pool isolation** — every control-plane call on the Supabase create-org lane
  is routed to the dedicated ``org`` pool, never the shared ``auth`` pool that
  authentication work occupies. Revert a ``pool="org"`` argument and the
  recording test fails.
* **One membership read** — the creator's active memberships are read ONCE per
  request: the value the eager graph init uses is threaded into
  ``provision_org`` so the post-RPC onboarding init does not re-read it. Delete
  the ``prior_org_ids`` pass-through and the call counter goes 1 -> 2.

An isolation measurement (not just an identity assertion) proves the pool is
actually free while every ``auth`` worker is parked.

Neither property touches the transport wait bound or the route exemption list
(#3834) — that is the point of keeping the fix here.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import threading
import time

import pytest

from tests.fake_control_plane import FakeControlPlane

_USER = "11111111-1111-1111-1111-111111111111"


# ── pool shape + isolation ──────────────────────────────────────────────────


def test_org_pool_is_separate_from_every_other_pool():
    import tortoise.monitoring as monitoring

    auth = monitoring.control_plane_worker("auth")
    org = monitoring.control_plane_worker("org")
    telemetry = monitoring.control_plane_worker("telemetry")
    graph = monitoring.control_plane_worker("graph")
    oauth = monitoring.control_plane_worker("oauth")

    assert org is not auth
    assert org is not telemetry
    assert org is not graph
    assert org is not oauth
    assert org.workers == monitoring.CONTROL_PLANE_ORG_WORKERS


def test_org_pool_does_not_queue_behind_a_saturated_auth_pool():
    """MEASURED isolation: park every ``auth`` worker, then run the same probe
    on both pools. The org probe completes; the auth probe cannot start.

    This is the local stand-in for the hosted contention this issue fixes —
    there is no local PostgREST, so the queueing itself is what is measured.
    """
    import tortoise.monitoring as monitoring

    auth = monitoring.control_plane_worker("auth")
    org = monitoring.control_plane_worker("org")
    width = auth.workers

    release = threading.Event()
    all_parked = threading.Barrier(width + 1, timeout=10)

    def _hold() -> None:
        all_parked.wait()
        release.wait(timeout=15)

    holders = [auth.submit(_hold) for _ in range(width)]
    all_parked.wait()  # every auth worker is now inside _hold
    try:
        org_start = time.monotonic()
        assert org.submit(lambda: "org-ok").result(timeout=2) == "org-ok"
        org_elapsed = time.monotonic() - org_start

        auth_future = auth.submit(lambda: "auth-ok")
        with pytest.raises(concurrent.futures.TimeoutError):
            auth_future.result(timeout=0.5)
    finally:
        release.set()
        for holder in holders:
            with contextlib.suppress(Exception):
                holder.result(timeout=5)

    assert org_elapsed < 0.5, f"org pool was not free: {org_elapsed:.3f}s"


# ── wiring: every create-org control-plane call rides the org pool ──────────


def test_create_org_supabase_lane_routes_every_call_to_the_org_pool(monkeypatch):
    import tortoise.hosted_api as ha
    import tortoise.supabase_control as sc

    fake = FakeControlPlane(
        {"organizations": [], "org_memberships": [], "api_keys": []})
    # one PRIOR owner membership (created long before the 1h rate window) so the
    # lane's single membership read has a value to thread through.
    fake.seed("org_memberships", [{
        "id": "m1", "org_id": "prior-org", "user_id": _USER,
        "role": "owner", "status": "active",
        "created_at": "2020-01-01T00:00:00Z",
    }])
    monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)

    eager_seen: list[list[str] | None] = []

    def _eager(cp, org_id, name, uid, prior_org_ids=None):
        eager_seen.append(prior_org_ids)
        return f"org_{org_id}"

    provision_seen: dict = {}

    def _provision(cp, **kwargs):
        provision_seen.update(kwargs)

    monkeypatch.setattr(ha, "_eager_provision_org_graph", _eager)
    monkeypatch.setattr(sc, "provision_org", _provision)

    seen: list[tuple[str, str]] = []
    real_offload = ha._cp_offload

    async def _record(fn, *, op, pool="auth", **kwargs):
        seen.append((op, pool))
        return await real_offload(fn, op=op, pool=pool, **kwargs)

    monkeypatch.setattr(ha, "_cp_offload", _record)

    out = asyncio.run(ha._create_org_supabase_lane(fake, "Acme", {"user_id": _USER}))

    assert out["name"] == "Acme" and out["tier"] == "free"
    assert seen, "no control-plane offload was recorded"
    assert all(pool == "org" for _op, pool in seen), seen
    assert [op for op, _pool in seen] == [
        "membership_count_since",
        "org_by_name",
        "owned_free_org_ids",
        "active_membership_org_ids",
        "provision_org",
    ], seen
    # the one membership read is handed to BOTH consumers (no second read)
    assert eager_seen == [["prior-org"]], eager_seen
    assert provision_seen.get("prior_org_ids") == ["prior-org"], provision_seen


# ── the duplicate read, removed at the hook seam ────────────────────────────


class _DummySdk:
    def _get_proj(self):
        return object()


def test_post_provision_hook_skips_the_membership_read_when_ids_are_given(monkeypatch):
    import tortoise.hosted_api as ha
    import tortoise.onboarding.state as onboarding_state
    import tortoise.supabase_control as sc

    fake = FakeControlPlane({"organizations": [], "org_memberships": []})
    reads: list[str] = []

    def _count(cp, uid):
        reads.append(uid)
        return []

    monkeypatch.setattr(sc, "active_membership_org_ids", _count)
    monkeypatch.setattr(onboarding_state, "read_prior_org_fork",
                        lambda proj, oid: None)
    monkeypatch.setattr(onboarding_state, "ensure_onboarding_state_node",
                        lambda proj, oid, **kwargs: None)
    monkeypatch.setattr(ha, "_make_sdk", lambda namespace: _DummySdk())

    params = {"p_org_id": "org1", "p_user_id": "u1"}
    sc._ensure_onboarding_node_after_provision(
        fake, "org1", params, prior_org_ids=["prior1"])
    assert reads == [], "the hook re-read memberships a caller already supplied"

    # legacy callers (register/signup/onboarding) still read it themselves
    sc._ensure_onboarding_node_after_provision(fake, "org1", params)
    assert reads == ["u1"], reads


def test_provision_org_forwards_prior_org_ids_without_reaching_the_rpc(monkeypatch):
    """The keyword is consumed by the hook and never placed in the RPC body."""
    import tortoise.supabase_control as sc

    captured: dict = {}
    hook_calls: list = []

    class _Cp:
        def rpc(self, fn, body):
            captured["fn"] = fn
            captured["body"] = body

    monkeypatch.setattr(sc, "_ensure_onboarding_node_after_provision",
                        lambda cp, org_id, params, **kw:
                        hook_calls.append((org_id, params, kw)))
    monkeypatch.setattr("tortoise.pack_state.ensure_tenant_packs",
                        lambda sdk: None)

    sc.provision_org(_Cp(), prior_org_ids=["prior1"],
                     p_org_id="org1", p_user_id="u1")

    assert captured["fn"] == "provision_team"
    assert "prior_org_ids" not in captured["body"]
    assert hook_calls and hook_calls[0][2] == {"prior_org_ids": ["prior1"]}


def test_org_create_active_membership_is_read_once_end_to_end(monkeypatch):
    """The end-to-end count a reviewer can read: exactly ONE
    ``active_membership_org_ids`` read for a full create-org request."""
    import tortoise.hosted_api as ha
    import tortoise.supabase_control as sc

    fake = FakeControlPlane(
        {"organizations": [], "org_memberships": [], "api_keys": []})
    fake.seed("org_memberships", [{
        "id": "m1", "org_id": "prior-org", "user_id": _USER,
        "role": "owner", "status": "active",
        "created_at": "2020-01-01T00:00:00Z",
    }])
    monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(ha, "_eager_provision_org_graph",
                        lambda cp, org_id, name, uid, prior_org_ids=None:
                        f"org_{org_id}")

    calls: list[str] = []
    real_memberships = sc.active_membership_org_ids

    def _count(cp, uid):
        calls.append(uid)
        return real_memberships(cp, uid)

    monkeypatch.setattr(sc, "active_membership_org_ids", _count)
    # the real provision_org runs the real hook, which now receives the ids.
    asyncio.run(ha._create_org_supabase_lane(fake, "Acme", {"user_id": _USER}))

    assert calls == [_USER], (
        f"active memberships were read {len(calls)} times for one create-org "
        "request — the duplicate read is back")


# ── the account-deletion gate stays OFF the bounded pool ───────────────────
#
# `create_org` runs the #4029 P2-6 delete-pending gate before the lane branch,
# for BOTH lanes, with a documented FAIL-OPEN contract on a read fault. A
# bounded control-plane pool adds a NEW fail-open trigger that is purely a
# LOCAL scheduling condition — a pool refusal (backlog full) or a bound miss
# would have to be read as "no row", disabling a security gate. The default
# executor's queue is unbounded, so this read stays there (its pre-#7678
# behavior); the `org` pool carries only the gate-only reads and the provision
# write. This pins the boundary so a later "route it too" change must confront
# the reason.


def test_create_org_deletion_gate_is_not_on_the_bounded_pool(monkeypatch):
    import tortoise.hosted_api as ha
    import tortoise.supabase_control as sc

    pools: list[str] = []
    read: list[str] = []

    async def _offload(fn, *, op, pool="auth", **kwargs):
        pools.append(pool)
        return fn()

    async def _lane(cp, name, user):
        return {"org_id": "o1", "name": name, "tier": "free"}

    monkeypatch.setattr(ha, "_cp_offload", _offload)
    monkeypatch.setattr(ha, "_create_org_supabase_lane", _lane)
    monkeypatch.setattr(sc, "get_control_plane", lambda: object())
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(sc, "account_deletion_row",
                        lambda cp, uid: read.append(uid))

    out = asyncio.run(ha.create_org({"name": "Acme"}, {"user_id": _USER}))

    assert out["org_id"] == "o1"
    # the read ran (on the default executor) and never entered the seam
    assert read == [_USER], read
    assert pools == [], pools


def test_create_org_fails_open_when_the_deletion_read_faults(monkeypatch):
    """The gate is fail-open on a read fault only — a present row still 403s."""
    import tortoise.hosted_api as ha
    import tortoise.supabase_control as sc

    async def _lane(cp, name, user):
        return {"org_id": "o1", "name": name, "tier": "free"}

    def _boom(cp, uid):
        raise RuntimeError("postgrest down")

    monkeypatch.setattr(ha, "_create_org_supabase_lane", _lane)
    monkeypatch.setattr(sc, "get_control_plane", lambda: object())
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(sc, "account_deletion_row", _boom)

    out = asyncio.run(ha.create_org({"name": "Acme"}, {"user_id": _USER}))
    assert out["org_id"] == "o1"


def test_create_org_still_refuses_when_a_deletion_row_is_present(monkeypatch):
    import tortoise.hosted_api as ha
    import tortoise.supabase_control as sc

    async def _lane(cp, name, user):
        return {"org_id": "o1", "name": name, "tier": "free"}

    monkeypatch.setattr(ha, "_create_org_supabase_lane", _lane)
    monkeypatch.setattr(sc, "get_control_plane", lambda: object())
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(sc, "account_deletion_row",
                        lambda cp, uid: {"user_id": _USER})

    with pytest.raises(ha.HTTPException) as excinfo:
        asyncio.run(ha.create_org({"name": "Acme"}, {"user_id": _USER}))
    assert excinfo.value.status_code == 403


def test_the_owned_org_REPLAY_also_rides_the_org_pool(monkeypatch):
    """#7677: the REPLAY is a control-plane read too, so it belongs on the org pool.

    The fresh-path pin above never reaches ``owned_org_replay`` — its op sequence
    ends at ``provision_org`` — so a replay added later could ride the shared
    ``auth`` pool while every sibling call rode ``org``, silently undoing #7686's
    isolation on exactly the retry path that exists BECAUSE auth contention
    crosses the transport bound (#4816). That is not hypothetical: it is what the
    #7677 replay call did until this test drove the duplicate path.
    """
    import tortoise.hosted_api as ha
    import tortoise.supabase_control as sc

    fake = FakeControlPlane({"organizations": [], "org_memberships": []})
    fake.seed("organizations", [{
        "id": "existing", "name": "Acme", "created_at": "2020-01-01T00:00:00Z",
        "deleted_at": None, "subscription_status": "active",
    }])
    fake.seed("org_memberships", [{
        "id": "m1", "org_id": "existing", "user_id": _USER,
        "role": "owner", "status": "active",
        "created_at": "2020-01-01T00:00:00Z",
    }])
    monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)

    seen: list[tuple[str, str]] = []
    real_offload = ha._cp_offload

    async def _record(fn, *, op, pool="auth", **kwargs):
        seen.append((op, pool))
        return await real_offload(fn, op=op, pool=pool, **kwargs)

    monkeypatch.setattr(ha, "_cp_offload", _record)

    out = asyncio.run(
        ha._create_org_supabase_lane(fake, "Acme", {"user_id": _USER}))

    ops = [op for op, _pool in seen]
    assert "owned_org_replay" in ops, (
        f"the duplicate path did not reach the replay, so this test does not "
        f"cover it: {seen}")
    assert all(pool == "org" for _op, pool in seen), (
        f"a control-plane call on the org-create lane did not ride the org pool "
        f"(#7686/#4816): {seen}")
    assert out.get("org_id") == "existing", out
