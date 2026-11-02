"""#7678 — the org-create control-plane path: a dedicated pool + one membership read.

Two properties, each with a falsifier that goes red on revert:

* **Pool isolation** — every control-plane call on the Supabase create-org lane
  is routed to the dedicated ``org`` pool, never the shared ``auth`` pool that
  authentication work occupies. Revert a ``pool="org"`` argument and the
  recording test fails. The ONE exception is a REGISTRY read: the prior-org
  listing probe is graph work and rides the ``graph`` pool through
  ``_graph_offload`` (#3718/#3773), which is why the recorded op list names its
  pool explicitly instead of asserting one pool for the whole lane.
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

    eager_seen: list[tuple] = []
    listed_stub = {"org_prior-org"}

    # #10510: the lane samples the server-wide listing off-loop too (the
    # prior-org open is gated on it), so keep this test off a real store read.
    monkeypatch.setattr(ha, "_registry_existing_graphs",
                        lambda: set(listed_stub))

    def _eager(cp, org_id, name, uid, prior_org_ids=None, prior_moved=None,
               prior_listed=None):
        eager_seen.append((prior_org_ids, prior_moved, prior_listed))
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
    # #10452 review P2 (round 2): the two prior-org facts are resolved off-loop
    # in TWO submissions because they ride DIFFERENT lanes — the
    # `organizations.graph_name` read is control-plane (`org`, #7678) and the
    # server-wide listing probe is a REGISTRY read (`graph`, #3718/#3773).
    assert seen == [
        ("membership_count_since", "org"),
        ("org_by_name", "org"),
        ("owned_free_org_ids", "org"),
        ("active_membership_org_ids", "org"),
        # both are handed to the eager init, which is sync and called directly
        # from this coroutine, so a read it issued would have run ON THE LOOP
        ("org_default_namespace", "org"),
        ("registry_existing_graphs", "graph"),
        ("provision_org", "org"),
    ], seen
    # the one membership read is handed to BOTH consumers (no second read),
    # and BOTH prior-org facts are threaded (a name alone is not a gate)
    assert eager_seen == [(["prior-org"], None, listed_stub)], eager_seen
    assert provision_seen.get("prior_org_ids") == ["prior-org"], provision_seen


# ── the listing probe's LANE CONTRACT (it is a graph read, #3718/#3773) ────


def _lane_with_a_prior_org(monkeypatch, *, probe=None):
    """A create-org lane whose user already owns one prior org, ready to run.

    Returns ``(ha, fake, submissions)``; ``submissions`` records every
    ``_cp_offload`` submission's kwargs, so a test can read the seam contract a
    call actually carried rather than infer it from prose.
    """
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
                        lambda cp, org_id, name, uid, **kw: f"org_{org_id}")
    monkeypatch.setattr(sc, "provision_org", lambda cp, **kwargs: None)
    monkeypatch.setattr(ha, "_registry_existing_graphs",
                        probe or (lambda: {"org_prior-org"}))

    submissions: list[dict] = []
    real = ha._cp_offload

    async def _record(fn, **kwargs):
        submissions.append(dict(kwargs))
        return await real(fn, **kwargs)

    monkeypatch.setattr(ha, "_cp_offload", _record)
    return ha, fake, submissions


def test_the_prior_org_listing_probe_rides_the_graph_lane(monkeypatch):
    """#10452 review P2 (round 2) — the listing probe is a REGISTRY read.

    FAIL-ON against the shape this replaces, which folded BOTH prior-org facts
    into ONE ``pool="org"`` submission to save a hand-off: there the probe's
    submission carried ``pool="org"``, ``timeout=None`` (the seam resolves
    that to the PostgREST bound, ``CONTROL_PLANE_OFFLOAD_TIMEOUT_S`` = 10 s —
    half the graph lane's documented cold-start allowance) and no
    ``unavailable`` factory (so a bound miss raised ``_control_plane_unavailable``
    — the SIGN-IN 503 body — on the org-create request path).

    The assertion is on the SUBMISSION, not on a docstring: the finding is
    about which seam the call entered and with what contract, and that is what
    this reads.
    """
    import tortoise.monitoring as monitoring

    ha, fake, submissions = _lane_with_a_prior_org(monkeypatch)

    asyncio.run(ha._create_org_supabase_lane(fake, "Acme", {"user_id": _USER}))

    probe = [s for s in submissions if s.get("op") == "registry_existing_graphs"]
    assert probe, (
        "the prior-org listing probe was not submitted at all — the #10510 "
        f"gate then has no listing to gate on: {[s.get('op') for s in submissions]}")
    assert probe[0]["pool"] == "graph", (
        f"the registry read rode the {probe[0]['pool']!r} pool — #3718/#3773 route "
        f"registry reads to the graph lane: {probe[0]}")
    assert probe[0]["timeout"] == pytest.approx(monitoring.graph_offload_timeout_s()), (
        "the listing probe was budgeted at a bound other than the graph lane's "
        f"cold-start allowance: {probe[0]['timeout']!r} vs "
        f"{monitoring.graph_offload_timeout_s()!r} ({probe[0]})")
    assert probe[0]["unavailable"] is ha._graph_unavailable, (
        "a bound miss on the registry read would raise the wrong 503 body — the "
        f"graph lane's copy is `_graph_unavailable`: {probe[0]}")

    # The OTHER prior-org fact did not move pools: `organizations.graph_name` is
    # a control-plane read and stays on this lane's `org` pool (#7678).
    namespace = [s for s in submissions if s.get("op") == "org_default_namespace"]
    assert namespace and namespace[0]["pool"] == "org", submissions
    assert namespace[0].get("timeout") is None, (
        "the control-plane read was given a foreign bound: "
        f"{namespace[0]}")


def test_a_bound_miss_on_the_listing_probe_is_a_graph_503_not_a_sign_in_503(monkeypatch):
    """#10452 review P2 (round 2) — the failure MODE, executed.

    The fold's own justification was that the probe is fail-soft, so folding it
    into the ``org`` submission "adds no new failure mode". The probe's internal
    exceptions are indeed swallowed — but a BOUND MISS is raised by the offload
    seam, not by the callable. So a slow registry probe failed `POST
    /v1/organizations` with the sign-in 503 body.

    FAIL-ON: under the folded shape this lane submits the probe with
    ``timeout=None`` on the ``org`` pool, so the forced 50 ms bound does not
    apply, the probe's own ``None``/listing returns, and NO 503 is raised at all
    — the ``pytest.raises`` reds. With the split, the graph lane's bound is the
    one that expires and it maps to ``graph_unavailable``.
    """
    release = threading.Event()

    def _slow_probe():
        release.wait(timeout=5)
        return {"org_prior-org"}

    ha, fake, _submissions = _lane_with_a_prior_org(monkeypatch, probe=_slow_probe)
    monkeypatch.setattr(ha, "graph_offload_timeout_s", lambda: 0.05)

    try:
        with pytest.raises(ha.HTTPException) as excinfo:
            asyncio.run(ha._create_org_supabase_lane(
                fake, "Acme", {"user_id": _USER}))
    finally:
        release.set()

    assert excinfo.value.status_code == 503
    detail = excinfo.value.detail
    assert detail["error_code"] == "graph_unavailable", (
        "a bound miss on the registry read must carry the graph lane's body — "
        "the sign-in copy would tell the caller to re-authenticate for a graph "
        f"capacity problem: {detail}")


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

        def query(self, table, *, select=None, filters=None, method="GET",
                  json_body=None, order=None, limit=None):
            # #10025: provisioning now also READS and MINTS the org's
            # `kind='default'` graphs row (ensure_default_graph_row), so a
            # control-plane double needs the query seam. This double exists to
            # pin the RPC BODY, so the graphs I/O is inert here — the mint's
            # own behaviour is pinned in tests/test_10025_graph_identity.py.
            return [] if method == "GET" else None

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
                        lambda cp, org_id, name, uid, prior_org_ids=None,
                        prior_moved=None, prior_listed=None:
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
