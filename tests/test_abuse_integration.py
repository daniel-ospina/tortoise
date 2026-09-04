"""Integration tests for abuse prevention (#308, plan Task 11).

Failure modes per the issue body:
1. false-positive suspension — single burst flags, never suspends; a breach
   persisting across the staging window suspends; un-suspend restores access
2. rate-limit bypass — suspension is durable (survives a process "restart"
   with the signal set cleared); signup-path key creates count (trigger);
   bootstrap mints don't
3. exfiltration detection — per-key AND team-fan-out read velocity notify;
   writes never count as reads

Plus: REST 403 SUSPENDED shape, MCP -32006 + warm-cache immediacy + restore,
R4 geo on both request classes, session-key mint gate, session-authed alerts
endpoint, Turnstile 400 on the signup endpoint, weighted R1 introspection.
#1913: the session-JWT REST lane runs the post-auth abuse hooks (R3 + R4).

Pattern: TestClient + FakeControlPlane (migration 0015 trigger/RPC emulation)
via monkeypatched get_control_plane / is_supabase_enabled / get_abuse_store —
the same seam pattern as test_hosted_api.py / test_flip_gate.py.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: I001

import tortoise.supabase_control as sc
from tortoise import abuse
from tortoise.abuse import SupabaseAbuseStore
from tortoise.auth import lookup_hash
from tests._http_fixtures import patched_tortoise_sdk
from tests.fake_control_plane import FakeControlPlane

TEAM = "team-abuse-1"
TOKEN_A = "tt_abuse_aaaa1111"
TOKEN_B = "tt_abuse_bbbb2222"

# #1719 (Task 3): team_memberships.user_id is a uuid column — real JWT
# subjects are UUIDs, so non-UUID user_id literals are prod-impossible
# (FakeControlPlane's fidelity check raises HTTP 400 on them). Identity /
# api_keys.created_by stay TEXT and remain non-UUID.
_U1 = "9f2c1a40-0000-4a00-8000-000000000001"
_U_STRANGER = "9f2c1a40-0000-4a00-8000-00000000000a"


# ── Fixtures ────────────────────────────────────────────────────────────────

def _seed_team(fake: FakeControlPlane, *, suspended=None, flagged=None):
    fake.seed("teams", [{
        "id": TEAM, "name": "abuse-team", "tier": "free",
        "email": "owner@abuse.test", "graph_name": f"team_{TEAM}",
        "max_users": 1, "max_graphs": 1, "ops_allowance": 10000,
        "graph_size_cap": 100000, "suspended_at": suspended,
        "flagged_at": flagged,
    }])
    for token, kid in ((TOKEN_A, "key-a"), (TOKEN_B, "key-b")):
        fake.seed("api_keys", [{
            "id": kid, "team_id": TEAM, "lookup_hash": lookup_hash(token),
            "key_prefix": token[:10], "created_via": "provisioned",
            "created_by": "user-1", "created_at": "2026-08-01T00:00:00+00:00",
            "expires_at": None, "revoked_at": None,
        }])
    # clear the trigger events the seeding itself emitted (seed() bypasses
    # POST — only query(POST) fires the emulated trigger)
    fake.tables.pop("abuse_events", None)
    return fake


@pytest.fixture
def fake():
    return _seed_team(FakeControlPlane())


@pytest.fixture
def notified(monkeypatch):
    calls: list[tuple[str, dict, dict]] = []
    monkeypatch.setattr("tortoise.notify.notify_abuse",
                        lambda kind, team, details=None:
                        calls.append((kind, team, details or {})))
    return calls


@pytest.fixture
def env(monkeypatch, fake, notified):
    """Supabase-mode seams over the fake plane + temp SDK DB + low R1
    threshold for a fast burst test."""
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
    monkeypatch.setattr(sc, "get_abuse_store",
                        lambda: SupabaseAbuseStore(fake))
    monkeypatch.setenv("TORTOISE_ABUSE_POINT_THRESHOLD", "5")
    monkeypatch.setenv("TORTOISE_ABUSE_POINT_WINDOW_S", "3600")
    abuse.set_engine(None)
    with abuse._SIGNAL_LOCK:
        abuse._SUSPENDED_SIGNAL.clear()
    abuse.reset_geo_cache()

    # fresh in-memory read tracker per test (module-level singleton otherwise
    # leaks counts across tests within the real-time 5-min window)
    abuse.READ_TRACKER = abuse.ReadVelocityTracker()

    import tortoise.hosted_api as ha
    app = ha.app
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "abuse.db")
        # #2127 wave 2: shared helper — patch __init__ → temp DB, #1950
        # TORTOISE_DB_PATH pin, close-then-clear at enter; pop-env → restore
        # __init__ → deterministic anchor close → clear overrides at exit.
        # Supersedes the local _patch_sdk_init/_restore_sdk_init (restore
        # was restore-init-only: no pin, no anchor close — the #1497/#2090
        # gap). set_engine(None) keeps running AFTER the helper exit, same
        # relative order as the old restore → set_engine pair.
        try:
            with patched_tortoise_sdk(db_path):
                yield {"fake": fake, "notified": notified, "app": app}
        finally:
            abuse.set_engine(None)


def _auth(token=TOKEN_A):
    return {"Authorization": f"Bearer {token}"}


# ── R5: REST suspension enforcement ─────────────────────────────────────────

class TestRestSuspension:
    def test_suspended_team_403_with_appeal(self, env):
        env["fake"].rpc("abuse_suspend", {"p_team_id": TEAM})
        with TestClient(env["app"]) as tc:
            r = tc.get("/v1/team", headers=_auth())
        assert r.status_code == 403
        detail = r.json()["detail"]
        assert detail["code"] == "SUSPENDED"
        assert detail["appeal_url"].startswith("http")

    def test_drift_resolution_clears_suspension_signal(self, env):
        """#1096 accepted-risk pin: under 0015 drift a fresh resolution reads
        suspended_at=None and the self-heal clears the in-process signal —
        the only local enforcement cell is torn down while the durable
        suspended_at stays stamped (the worst-case window mechanism)."""
        fake = env["fake"]
        fake.rpc("abuse_suspend", {"p_team_id": TEAM})
        abuse.mark_suspended(TEAM)
        assert abuse.is_suspended_signal(TEAM)
        fake.missing_columns = {"teams": {"suspended_at", "flagged_at"}}
        with TestClient(env["app"]) as tc:
            r = tc.get("/v1/team", headers=_auth())
        assert r.status_code == 200  # degraded (accepted-by-scope)
        assert not abuse.is_suspended_signal(TEAM)  # self-heal tore it down
        assert fake.tables["teams"][0]["suspended_at"] is not None  # durable stays

    def test_unsuspend_restores_next_request(self, env):
        fake = env["fake"]
        fake.rpc("abuse_suspend", {"p_team_id": TEAM})
        with TestClient(env["app"]) as tc:
            assert tc.get("/v1/team/keys", headers=_auth()).status_code == 403
            fake.rpc("abuse_unsuspend", {"p_team_id": TEAM})
            assert tc.get("/v1/team/keys", headers=_auth()).status_code == 200

    def test_suspension_survives_restart(self, env):
        """Bypass failure mode: durable state rejects even with the process
        signal set cleared (simulated restart)."""
        fake = env["fake"]
        fake.rpc("abuse_suspend", {"p_team_id": TEAM})
        abuse.mark_suspended(TEAM)
        # simulate a worker restart: signal set + engine wiped
        with abuse._SIGNAL_LOCK:
            abuse._SUSPENDED_SIGNAL.clear()
        abuse.set_engine(None)
        with TestClient(env["app"]) as tc:
            r = tc.get("/v1/team/keys", headers=_auth())
        assert r.status_code == 403  # durable suspended_at is the authority


# ── R1: point burst → flag, never suspend in one window ────────────────────

class TestPointBurst:
    def _post_point(self, tc, i):
        r = tc.post("/v1/points", headers=_auth(),
                    json={"content": f"burst point {i}", "kind": "statement"})
        assert r.status_code == 200, r.text
        return r

    def test_burst_flags_but_never_suspends(self, env):
        """False-positive failure mode: 6 points (> threshold 5) inside one
        window flag the team; further writes in-window stay 'breach'."""
        fake = env["fake"]
        with TestClient(env["app"]) as tc:
            for i in range(6):
                self._post_point(tc, i)
            team_row = fake.tables["teams"][0]
            assert team_row["flagged_at"] is not None      # stage 1
            assert team_row["suspended_at"] is None        # never stage 2
            assert not abuse.is_suspended_signal(TEAM)
            for i in range(3):  # continued in-window writes: still no suspend
                self._post_point(tc, i)
            assert fake.tables["teams"][0]["suspended_at"] is None
            assert tc.get("/v1/team/keys", headers=_auth()).status_code == 200
            # quiet → burst AFTER the window: a new episode re-flags, never
            # suspends on its first evaluation (stale-flag protection)
            past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()  # noqa: UP017
            for e in fake.tables["abuse_events"]:
                if e["event_type"] in ("flag", "point_create"):
                    e["created_at"] = past
            self._post_point(tc, 50)
            for i in range(5):
                self._post_point(tc, 51 + i)
            assert fake.tables["teams"][0]["suspended_at"] is None
        assert [c[0] for c in env["notified"]].count("abuse_flag") >= 2

    def test_boundary_crossing_suspends_and_403(self, env):
        """A breach persisting past flagged_at + window suspends (delta 13).
        The fake clock is compressed: age flagged_at 2h into the past while
        the window sum stays over threshold."""
        fake = env["fake"]
        with TestClient(env["app"]) as tc:
            for i in range(6):
                self._post_point(tc, i)
            assert fake.tables["teams"][0]["flagged_at"] is not None
            # Age the flag EPISODE anchor (the flag event row — staging is
            # event-derived) a full window into the past, and move ONE point
            # event into the continuity band (flag, now-window] so the breach
            # genuinely spans the boundary. The rest stay fresh → the window
            # sum still breaches.
            past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()  # noqa: UP017
            band = (datetime.now(timezone.utc) - timedelta(minutes=90)).isoformat()  # noqa: UP017
            flag_rows = [e for e in fake.tables["abuse_events"]
                         if e["event_type"] == "flag"]
            flag_rows[0]["created_at"] = past
            point_rows = [e for e in fake.tables["abuse_events"]
                          if e["event_type"] == "point_create"]
            point_rows[0]["created_at"] = band  # continuity evidence
            self._post_point(tc, 99)  # evaluation now crosses the boundary
            assert fake.tables["teams"][0]["suspended_at"] is not None
            # next authed request 403s with the SUSPENDED contract
            r = tc.get("/v1/team/keys", headers=_auth())
            assert r.status_code == 403
            assert r.json()["detail"]["code"] == "SUSPENDED"
        assert "abuse_suspended" in [c[0] for c in env["notified"]]

    def test_non_create_writes_never_trip_r1(self, env):
        """Delta 8: only Point CREATION records. REST has no point-update
        endpoint (purity by construction); the non-create write surface here
        is key revocation — revokes record NO point_create events. (MCP
        update/supersede/invalidate/retract purity is asserted by the
        source-introspection test below.)"""
        fake = env["fake"]
        fake.seed("api_keys", [{
            "id": "revoke-me", "team_id": TEAM,
            "lookup_hash": "revoke-hash", "key_prefix": "tt_revoke12",
            "created_via": "recovery", "created_by": "user-1",
            "created_at": "2026-08-01T00:00:00+00:00",
            "expires_at": None, "revoked_at": None,
        }])
        with TestClient(env["app"]) as tc:
            for _ in range(6):
                r = tc.delete("/v1/team/keys/revoke-me", headers=_auth())
                assert r.status_code in (200, 404), r.text
        events = [e for e in fake.tables.get("abuse_events", [])
                  if e["event_type"] == "point_create"]
        assert events == []  # revokes never record point_create
        assert env["fake"].tables["teams"][0]["flagged_at"] is None


# ── R2: key-create velocity ─────────────────────────────────────────────────

class TestKeyVelocity:
    def _provision(self, fake, lookup):
        fake.rpc("provision_team", {
            "p_user_id": None, "p_identity": f"id-{lookup[:8]}",
            "p_team_id": TEAM, "p_team_name": "abuse-team",
            "p_api_key": f"tt_{lookup}", "p_key_hash": "kh",
            "p_lookup_hash": lookup, "p_graph_name": f"team_{TEAM}",
            "p_email": f"{lookup[:8]}@x.co", "p_key_prefix": TEAM[:8],
        })

    def test_signup_path_key_creates_counted(self, env):
        """Bypass failure mode: the trigger records signup-RPC key creates;
        they evaluate on the team's next hooked request (R2 piggyback)."""
        fake = env["fake"]
        for i in range(11):  # > 10 per 24h
            self._provision(fake, f"lookup{i:04d}xxxx")
        events = [e for e in fake.tables["abuse_events"]
                  if e["event_type"] == "key_create"]
        assert len(events) == 11
        with TestClient(env["app"]) as tc:
            r = tc.post("/v1/points", headers=_auth(),
                        json={"content": "next hooked request",
                              "kind": "statement"})
            assert r.status_code == 200
        assert fake.tables["teams"][0]["flagged_at"] is not None
        flag_notifies = [c for c in env["notified"] if c[0] == "abuse_flag"]
        assert flag_notifies and flag_notifies[0][2]["rule"] == "key_create"

    def test_bootstrap_mints_excluded(self, env):
        """Delta 9: 11 bootstrap session mints produce zero key_create events
        and no flag (normal dashboard churn must not suspend)."""
        fake = env["fake"]
        for i in range(11):
            fake.query("api_keys", method="POST", json_body={
                "id": f"boot-{i}", "team_id": TEAM,
                "lookup_hash": f"boot-hash-{i}", "created_via": "bootstrap"})
        events = [e for e in fake.tables.get("abuse_events", [])
                  if e["event_type"] == "key_create"]
        assert events == []
        with TestClient(env["app"]) as tc:
            r = tc.post("/v1/points", headers=_auth(),
                        json={"content": "hooked", "kind": "statement"})
            assert r.status_code == 200
        assert fake.tables["teams"][0]["flagged_at"] is None


# ── R3: exfiltration detection ──────────────────────────────────────────────

class TestExfiltration:
    def test_per_key_velocity_notifies_once(self, env):
        with TestClient(env["app"]) as tc:
            for _ in range(101):
                assert tc.get("/v1/team/keys", headers=_auth()).status_code == 200
        velocity = [c for c in env["notified"]
                    if c[0] == "abuse_read_velocity"]
        assert len(velocity) == 1
        assert velocity[0][2]["scope"] == "key"
        # key-scope breach notifies the team OWNER (email resolved), not ops
        assert velocity[0][1]["email"] == "owner@abuse.test"
        # the breach also lands in the dashboard alert history
        alerts = SupabaseAbuseStore(env["fake"]).recent_alerts(TEAM)
        assert any(a["type"] == "read_velocity" for a in alerts)

    def test_fanout_team_velocity(self, env):
        """Two keys under the per-key limit, 101 together → team notify."""
        with TestClient(env["app"]) as tc:
            for _ in range(60):
                tc.get("/v1/team/keys", headers=_auth(TOKEN_A))
            for _ in range(40):
                tc.get("/v1/team/keys", headers=_auth(TOKEN_B))
            assert not [c for c in env["notified"]
                        if c[0] == "abuse_read_velocity"]
            tc.get("/v1/team/keys", headers=_auth(TOKEN_B))  # 101st team read
        velocity = [c for c in env["notified"]
                    if c[0] == "abuse_read_velocity"]
        assert len(velocity) == 1 and velocity[0][2]["scope"] == "team"

    def test_writes_not_counted_as_reads(self, env):
        """Delta 11: a POST burst never fires read velocity."""
        with TestClient(env["app"]) as tc:
            for i in range(120):  # noqa: B007
                pass  # threshold math below uses GET-only counting
            for i in range(101):
                tc.post("/v1/points", headers=_auth(),
                        json={"content": f"write {i}", "kind": "statement"})
        assert not [c for c in env["notified"]
                    if c[0] == "abuse_read_velocity"]


# ── R4: geo ─────────────────────────────────────────────────────────────────

class TestGeo:
    def test_new_country_notifies_then_quiet(self, env):
        fake = env["fake"]
        with TestClient(env["app"]) as tc:
            tc.get("/v1/team/keys", headers={**_auth(), "CF-IPCountry": "US"})
            assert [c[0] for c in env["notified"]] == ["abuse_new_ip"]
            tc.get("/v1/team/keys", headers={**_auth(), "CF-IPCountry": "US"})
            assert len(env["notified"]) == 1  # seen country stays quiet
            tc.get("/v1/team/keys", headers={**_auth(), "CF-IPCountry": "DE"})
            assert len(env["notified"]) == 2
        ip_events = [e for e in fake.tables["abuse_events"]
                     if e["event_type"] == "auth_ip"]
        assert {e["country"] for e in ip_events} == {"US", "DE"}
        # R4 notifies the OWNER (email resolved from the team row)
        geo = [c for c in env["notified"] if c[0] == "abuse_new_ip"]
        assert geo and all(c[1]["email"] == "owner@abuse.test" for c in geo)

    def test_no_header_fail_open(self, env):
        with TestClient(env["app"]) as tc:
            assert tc.get("/v1/team/keys", headers=_auth()).status_code == 200
        assert env["notified"] == []


# ── MCP transport ───────────────────────────────────────────────────────────

class TestSessionLane:
    """#1913: the session-JWT REST lane runs the same post-auth abuse
    evaluation as the key lanes (R3 read velocity + R4 geo). The key lanes
    call _abuse_post_auth (get_current_team / _get_current_team_supabase);
    _session_user_team — the session lane — never did: session-driven GETs
    from a new country recorded no auth_ip event and didn't count toward R3.
    """

    def _seed_membership(self, fake):
        fake.seed("team_memberships", [{
            "user_id": _U1, "team_id": TEAM, "role": "owner",
            "status": "active", "team_name": "abuse-team"}])

    def _ip_events(self, fake):
        return [e for e in fake.tables["abuse_events"]
                if e["event_type"] == "auth_ip"]

    def test_session_get_records_auth_ip_and_read(self, env):
        """A session-lane GET from a new country records an auth_ip event
        (R4) and counts toward R3 team read velocity."""
        from starlette.datastructures import Headers
        from starlette.requests import Request

        from tortoise.hosted_api import _session_user_team
        fake = env["fake"]
        self._seed_membership(fake)
        # Direct unit call — the exact function the #1913 fix touches. A
        # full-stack session JWT would need JWKS machinery; get_current_team_
        # session calls get_current_user directly (no DI override), so the
        # session resolver is the precise seam.
        request = Request({
            "type": "http", "method": "GET", "path": "/v1/team/keys",
            "query_string": b"",
            "headers": Headers({"cf-ipcountry": "MX"}).raw,
        })
        team = asyncio.run(_session_user_team(request, {"user_id": _U1}))
        assert team["team_id"] == TEAM
        # R4: the new-country session request recorded auth_ip + notified
        assert {e["country"] for e in self._ip_events(fake)} == {"MX"}
        geo = [c for c in env["notified"] if c[0] == "abuse_new_ip"]
        assert len(geo) == 1 and geo[0][2]["country"] == "MX"
        # R3: the session GET counted toward team read velocity
        assert len(abuse.READ_TRACKER._by_team[TEAM]) == 1

    def test_key_lane_unchanged(self, env):
        """Regression: the key lane still records auth_ip (R4) + R3 reads
        after the session-lane fix — behavior unchanged."""
        fake = env["fake"]
        with TestClient(env["app"]) as tc:
            r = tc.get("/v1/team/keys",
                       headers={**_auth(), "CF-IPCountry": "CL"})
            assert r.status_code == 200
        assert {e["country"] for e in self._ip_events(fake)} == {"CL"}
        assert [c[0] for c in env["notified"]] == ["abuse_new_ip"]
        assert len(abuse.READ_TRACKER._by_team[TEAM]) == 1


class TestMcp:
    def _mcp_client(self):
        """Minimal Starlette app wrapped ONLY in TeamResolutionMiddleware —
        exercises the same resolution/suspension/geo path the /mcp mount uses."""
        from starlette.applications import Starlette  # noqa: I001
        from starlette.responses import JSONResponse
        from starlette.routing import Route
        from tortoise.mcp_auth import TeamResolutionMiddleware

        async def echo(request):
            await request.body()
            return JSONResponse({"ok": True})

        app = Starlette(routes=[Route("/", echo, methods=["POST"])])
        app.add_middleware(TeamResolutionMiddleware)
        return TestClient(app)

    def test_suspended_team_jsonrpc_error(self, env):
        env["fake"].rpc("abuse_suspend", {"p_team_id": TEAM})
        with self._mcp_client() as tc:
            r = tc.post("/", headers=_auth(),
                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r.status_code == 403
        err = r.json()["error"]
        assert err["code"] == -32006
        assert err["data"]["code"] == "SUSPENDED"
        assert err["data"]["appeal_url"].startswith("http")

    def test_warm_cache_immediacy_and_restore(self, env):
        """Delta 14: a suspension lands while the token's 60s LRU entry is
        warm — the signal forces fresh resolution (immediate 403); un-suspend
        self-heals on the next request (AC8)."""
        fake = env["fake"]
        with self._mcp_client() as tc:
            assert tc.post("/", headers=_auth(),
                           json={"jsonrpc": "2.0", "id": 1,
                                 "method": "tools/list"}).status_code == 200
            # suspend (engine path: durable RPC + signal)
            fake.rpc("abuse_suspend", {"p_team_id": TEAM})
            abuse.mark_suspended(TEAM)
            r = tc.post("/", headers=_auth(),
                        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            assert r.status_code == 403
            assert r.json()["error"]["code"] == -32006
            # un-suspend: durable clear + the next fresh resolution evicts
            fake.rpc("abuse_unsuspend", {"p_team_id": TEAM})
            r = tc.post("/", headers=_auth(),
                        json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
            assert r.status_code == 200
            assert not abuse.is_suspended_signal(TEAM)

    def test_geo_on_mcp_transport(self, env):
        """Delta 10: R4 runs on the MCP transport too (attacker using only
        the agent SDK from a new country is still detected)."""
        with self._mcp_client() as tc:
            tc.post("/", headers={**_auth(), "CF-IPCountry": "BR"},
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert [c[0] for c in env["notified"]] == ["abuse_new_ip"]
        assert env["notified"][0][2]["country"] == "BR"


# ── Mint gate + alerts + status ─────────────────────────────────────────────

class TestMintGateAndAlerts:
    def test_mint_rejected_while_suspended(self, env):
        from tortoise.hosted_api import get_current_user
        fake = env["fake"]
        fake.seed("team_memberships", [{
            "user_id": _U1, "team_id": TEAM, "role": "owner",
            "status": "active", "team_name": "abuse-team"}])
        fake.rpc("abuse_suspend", {"p_team_id": TEAM})
        env["app"].dependency_overrides[get_current_user] = \
            lambda: {"user_id": _U1}
        try:
            with TestClient(env["app"]) as tc:
                r = tc.post("/v1/session/key",
                            json={"purpose": "recovery", "team_id": TEAM})
            assert r.status_code == 403
            assert r.json()["detail"]["code"] == "SUSPENDED"
        finally:
            env["app"].dependency_overrides.clear()

    def test_mint_allowed_under_0015_drift_then_blocked_after_recovery(self, env):
        """#1096 accepted-risk pin: under 0015 drift the mint gate's
        suspended_at reads None → a durably-suspended team CAN mint; after
        recovery the 403 returns (the fail-open window closes)."""
        from tortoise.hosted_api import get_current_user
        fake = env["fake"]
        fake.seed("team_memberships", [{
            "user_id": _U1, "team_id": TEAM, "role": "owner",
            "status": "active", "team_name": "abuse-team"}])
        # env fixture pre-seeds 2 provisioned keys at the free-tier cap
        # (max_api_keys=2). The DRIFT-phase mint has the suspension gate
        # bypassed (degrade) and would 402 at the cap before creating a
        # key — clear them so the 200 assertion holds. The recovery phase
        # 403s at the suspension gate before any cap check.
        # (precedent: test_auth_flip test_mint_resolves_then_revoked_rejected).
        fake.tables["api_keys"] = []
        fake.rpc("abuse_suspend", {"p_team_id": TEAM})
        fake.missing_columns = {"teams": {"suspended_at", "flagged_at"}}
        env["app"].dependency_overrides[get_current_user] = \
            lambda: {"user_id": _U1}
        try:
            with TestClient(env["app"]) as tc:
                r = tc.post("/v1/session/key",
                            json={"purpose": "recovery", "team_id": TEAM})
                assert r.status_code == 200  # fail-open during drift (accepted)
                assert "key" in r.json()  # the grant actually minted
            fake.missing_columns = None  # drift resolved
            with TestClient(env["app"]) as tc:
                r = tc.post("/v1/session/key",
                            json={"purpose": "recovery", "team_id": TEAM})
            assert r.status_code == 403
            # Gate identity: the SUSPENDED gate specifically (not another 403).
            assert r.json()["detail"]["code"] == "SUSPENDED"
        finally:
            env["app"].dependency_overrides.clear()

    def test_alerts_endpoint_session_authed(self, env):
        from tortoise.hosted_api import get_current_user
        fake = env["fake"]
        fake.seed("team_memberships", [{
            "user_id": _U1, "team_id": TEAM, "role": "owner",
            "status": "active", "team_name": "abuse-team"}])
        store = SupabaseAbuseStore(fake)
        store.flag_team(TEAM, "point_create", {"count": 6})
        store.record_event(TEAM, "auth_ip", country="US")
        env["app"].dependency_overrides[get_current_user] = \
            lambda: {"user_id": _U1}
        try:
            with TestClient(env["app"]) as tc:
                r = tc.get(f"/v1/team/alerts?team_id={TEAM}")
            assert r.status_code == 200
            types = {a["type"] for a in r.json()["alerts"]}
            assert "flag" in types and "auth_ip" in types
        finally:
            env["app"].dependency_overrides.clear()

    def test_alerts_reachable_while_suspended(self, env):
        """Delta 12: the alert history must stay visible during suspension
        (session auth — the API-key routes 403 by design)."""
        from tortoise.hosted_api import get_current_user
        fake = env["fake"]
        fake.seed("team_memberships", [{
            "user_id": _U1, "team_id": TEAM, "role": "owner",
            "status": "active", "team_name": "abuse-team"}])
        SupabaseAbuseStore(fake).flag_team(TEAM, "point_create", {"count": 6})
        fake.rpc("abuse_suspend", {"p_team_id": TEAM})
        env["app"].dependency_overrides[get_current_user] = \
            lambda: {"user_id": _U1}
        try:
            with TestClient(env["app"]) as tc:
                r = tc.get(f"/v1/team/alerts?team_id={TEAM}")
            assert r.status_code == 200
            assert any(a["type"] == "flag" for a in r.json()["alerts"])
        finally:
            env["app"].dependency_overrides.clear()

    def test_alerts_require_membership(self, env):
        from tortoise.hosted_api import get_current_user
        env["app"].dependency_overrides[get_current_user] = \
            lambda: {"user_id": _U_STRANGER}
        try:
            with TestClient(env["app"]) as tc:
                assert tc.get(f"/v1/team/alerts?team_id={TEAM}").status_code == 403
        finally:
            env["app"].dependency_overrides.clear()

    def test_team_info_status_flagged(self, env):
        """status ∈ {active, flagged} over HTTP — suspension 403s earlier."""
        fake = env["fake"]
        fake.tables["teams"][0]["flagged_at"] = \
            datetime.now(timezone.utc).isoformat()  # noqa: UP017
        with TestClient(env["app"]) as tc:
            r = tc.get("/v1/team", headers=_auth())
        assert r.status_code == 200
        assert r.json()["status"] == "flagged"


# ── Turnstile on the signup endpoint ────────────────────────────────────────

class TestTurnstileSignup:
    def test_captcha_400_when_challenge_fails(self, monkeypatch, env):
        monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret")
        monkeypatch.setattr("httpx.post",
                            lambda *a, **k: _Resp({"success": False}))
        with TestClient(env["app"]) as tc:
            r = tc.post("/v1/signup/email", json={
                "email": "cap@x.co", "password": "hunter2secret",
                "cf-turnstile-response": "bad-token"})
        assert r.status_code == 400
        assert "security check" in r.json()["detail"]

    def test_captcha_passes_and_flow_continues(self, monkeypatch, env):
        monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret")
        monkeypatch.setattr("httpx.post",
                            lambda *a, **k: _Resp({"success": True}))
        with TestClient(env["app"]) as tc:
            r = tc.post("/v1/signup/email", json={
                "email": "cap@x.co", "password": "hunter2secret",
                "cf-turnstile-response": "good-token"})
        # NOT the CAPTCHA 400 — the flow continued past siteverify (503 here:
        # SUPABASE_URL unset in this test env, the endpoint's own guard).
        assert r.status_code != 400

    def test_fail_open_without_secret(self, monkeypatch, env):
        monkeypatch.delenv("TURNSTILE_SECRET_KEY", raising=False)
        with TestClient(env["app"]) as tc:
            r = tc.post("/v1/signup/email", json={
                "email": "open@x.co", "password": "hunter2secret"})
        assert r.status_code != 400


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


# ── Introspection: weight map + write set (plan Task 11) ───────────────────

class TestIntrospection:
    def test_write_tool_names_cover_all_quota_gated(self):
        from tortoise.mcp_server import WRITE_TOOL_NAMES, _QUOTA_GATED  # noqa: I001
        assert WRITE_TOOL_NAMES >= _QUOTA_GATED
        assert "tortoise_ingest" in WRITE_TOOL_NAMES
        assert "tortoise_onboarding_demo_create" in WRITE_TOOL_NAMES

    def test_point_creating_wrap_sites_have_weights(self):
        """Dual membership by source: every Point-creating _quota_gated wrap
        passes abuse_weight; update-family wraps do not (delta 8 drift guard
        — a future Point-creating tool cannot silently re-open the bypass)."""
        import re  # noqa: F401, I001
        import tortoise.mcp_server as ms
        src = Path(ms.__file__).read_text()

        def _wrap_window(method):
            # exact wrap-site match (method + comma) — a bare prefix would
            # let e.g. ingest_corpus masquerade as ingest
            i = src.find(f"_get_team_sdk().{method},")
            assert i != -1, f"wrap site for {method} not found"
            return src[i:i + 260]

        for method in ("create_point", "create_operator", "mitigate_operator",
                       "file_decision", "file_human_approval", "diary_write",
                       "ingest", "checkpoint"):
            window = _wrap_window(method)
            assert "abuse_weight" in window, f"{method} lacks an R1 weight"
        for method in ("update_point", "supersede", "invalidate_point",
                       "retract_point"):
            window = _wrap_window(method)
            assert "abuse_weight" not in window, \
                f"{method} must NOT record point_create"

    def test_no_write_tool_counted_as_read(self):
        """Drift guard (code-review fix — the old version was a tautology):
        extract the ACTUAL _quota_gated wrap sites from source and assert
        every one is classified as a write in WRITE_TOOL_NAMES via the pinned
        method→tool map. A new wrapped write tool with no map entry fails
        here; a mapped tool missing from WRITE_TOOL_NAMES fails here too."""
        import re  # noqa: I001
        import tortoise.mcp_server as ms
        src = Path(ms.__file__).read_text()
        wrapped_methods = set(re.findall(
            r"_quota_gated\(_get_team_sdk\(\)\.(\w+)", src))
        # pinned method→tool map (the write surface as designed)
        method_to_tool = {
            "create_point": "tortoise_create_point",
            "update_point": "tortoise_update_point",
            "create_operator": "tortoise_create_operator",
            "mitigate_operator": "tortoise_mitigate_operator",
            "file_decision": "tortoise_file_decision",
            "file_human_approval": "tortoise_file_human_approval",
            "invalidate_point": "tortoise_invalidate",
            "supersede": "tortoise_supersede",
            "retract_point": "tortoise_retract_point",
            "checkpoint": "tortoise_checkpoint",
            "diary_write": "tortoise_diary_write",
            "create_entity": "tortoise_create_entity",
            "update": "tortoise_update",
            "annotate_operator": "tortoise_operator_action",
            "create_subject": "tortoise_create_subject",
            "create_object": "tortoise_create_object",
            "create_event": "tortoise_create_event",
            "create_document": "tortoise_create_document",
            "create_source": "tortoise_create_source",
            "assess_source": "tortoise_assess_source",
            "update_entity": "tortoise_update_entity",
            "create_edge": "tortoise_create_edge",
            "ingest": "tortoise_ingest",
            "index_directory": "tortoise_index_files",  # #1043 index path (Sources/Documents + edges) — quota-gated write
        }
        # every wrap site must be a known write method (new wrap → fail here)
        unknown = wrapped_methods - set(method_to_tool)
        assert not unknown, f"unmapped _quota_gated wrap sites: {unknown}"
        # every wrap site's tool must be classified as a WRITE (never a read)
        missing = {method_to_tool[m] for m in wrapped_methods} - ms.WRITE_TOOL_NAMES
        assert not missing, f"write tools missing from WRITE_TOOL_NAMES: {missing}"

    def test_destructive_mutating_tools_never_read_classified(self):
        """C5 #2114 (code-review P1): the NON-wrapped destructive/mutating
        tools carry _rw() annotations but bypass _quota_gated — they must
        still be classified as writes (WRITE_TOOL_NAMES) so the MCP scope
        gate (graphs:write required) + read-velocity metering treat them as
        writes. A graphs:read-only key invoking any of these would otherwise
        be a write-scope bypass (deleting points/entities, mutating
        operators/sources)."""
        import tortoise.mcp_server as ms
        destructive = {
            "tortoise_delete_point",      # DESTRUCTIVE — cannot be undone
            "tortoise_delete",            # destructive
            "tortoise_delete_entity",     # destructive
            "tortoise_set_point_baseline",  # mutates claims
            "tortoise_set_source_tier",   # mutates source metadata
            "tortoise_annotate_operator",  # mutates operator state
        }
        missing = destructive - ms.WRITE_TOOL_NAMES
        assert not missing, (
            f"destructive/mutating tools missing from WRITE_TOOL_NAMES "
            f"(a graphs:read-only MCP key could invoke them): {missing}")

    def test_mcp_read_hook_classification(self, monkeypatch):
        """maybe_record_mcp_read: writes skipped, reads counted, selfhost and
        no-team skipped, kill-switch respected."""
        import tortoise.mcp_server as ms  # noqa: I001
        import tortoise.abuse as abuse_mod
        calls = []
        monkeypatch.setattr(abuse_mod, "record_read",
                            lambda key_id, team_id, now=None:
                            calls.append((key_id, team_id)))
        ms.maybe_record_mcp_read("tortoise_search", "team-x",
                                 {"key_id": "k1"})
        assert calls == [("k1", "team-x")]
        ms.maybe_record_mcp_read("tortoise_create_point", "team-x",
                                 {"key_id": "k1"})
        ms.maybe_record_mcp_read("tortoise_ingest", "team-x", {"key_id": "k1"})
        assert len(calls) == 1  # write tools never count as reads
        ms.maybe_record_mcp_read("tortoise_search", "", {})
        ms.maybe_record_mcp_read("tortoise_search", "selfhost", {})
        assert len(calls) == 1  # no-team + selfhost skipped
        monkeypatch.setenv("TORTOISE_ABUSE_DISABLED", "1")
        ms.maybe_record_mcp_read("tortoise_search", "team-x", {})
        assert len(calls) == 1  # kill-switch respected

    def test_weight_arithmetic_pins(self):
        """The bulk-weight arithmetic AC1 depends on, pinned against the
        SDK result shapes (a shape drift → weight 0 → R1 bypass on the
        largest bulk surface; code-review P2)."""
        ingest_result = {"granularity": "bulk",
                         "created": {"points": 613, "entities": 2,
                                     "sources": 1, "connections": 4}}
        w_ingest = int(((ingest_result or {}).get("created") or {}).get("points") or 0)
        assert w_ingest == 613
        checkpoint_result = {"filed": 42, "duplicates": 3}
        w_checkpoint = int((checkpoint_result or {}).get("filed") or 0)
        assert w_checkpoint == 42
        options, evidence = ["a", "b"], [{"u": 1}]
        w_decision = 1 + len(options or []) + len(evidence or [])
        assert w_decision == 4
        # degenerate shapes must degrade to 0, never raise
        assert int(((None or {}).get("created") or {}).get("points") or 0) == 0
        assert int(({}).get("filed") or 0) == 0
        # and the REAL call sites still read those exact shape keys (drift
        # tripwire: a renamed SDK return key fails here, not in production)
        import tortoise.mcp_server as ms
        src = Path(ms.__file__).read_text()
        i_ingest = src.find("_get_team_sdk().ingest,")
        assert i_ingest != -1 and '.get("points")' in src[i_ingest:i_ingest + 400]
        i_ckpt = src.find("_get_team_sdk().checkpoint,")
        assert i_ckpt != -1 and '.get("filed")' in src[i_ckpt:i_ckpt + 400]
        # capture_session weight lives in hosted_api (REST seam)
        import tortoise.hosted_api as ha
        ha_src = Path(ha.__file__).read_text()
        i_cap = ha_src.find("len(body.conversation) + len(extracted)")
        assert i_cap != -1  # capture_session weights actual created Points
