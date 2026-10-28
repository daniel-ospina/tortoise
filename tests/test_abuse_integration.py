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
import re
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
from tortoise.abuse import ALERT_TYPES, SupabaseAbuseStore, _alert_dict
from tortoise.auth import lookup_hash
from tests._http_fixtures import patched_tortoise_sdk
from tests._verdict import verdict_client
from tests.fake_control_plane import FakeControlPlane

TEAM = "team-abuse-1"
TOKEN_A = "tt_abuse_aaaa1111"
TOKEN_B = "tt_abuse_bbbb2222"

# #1719 (Task 3): org_memberships.user_id is a uuid column — real JWT
# subjects are UUIDs, so non-UUID user_id literals are prod-impossible
# (FakeControlPlane's fidelity check raises HTTP 400 on them). Identity /
# api_keys.created_by stay TEXT and remain non-UUID.
_U1 = "9f2c1a40-0000-4a00-8000-000000000001"
_U_STRANGER = "9f2c1a40-0000-4a00-8000-00000000000a"


# ── Fixtures ────────────────────────────────────────────────────────────────

def _seed_team(fake: FakeControlPlane, *, suspended=None, flagged=None):
    fake.seed("organizations", [{
        "id": TEAM, "name": "abuse-team", "tier": "free",
        "email": "owner@abuse.test", "graph_name": f"org_{TEAM}",
        "max_users": 1, "max_graphs": 1, "ops_allowance": 10000,
        "graph_size_cap": 100000, "suspended_at": suspended,
        "flagged_at": flagged,
    }])
    for token, kid in ((TOKEN_A, "key-a"), (TOKEN_B, "key-b")):
        fake.seed("api_keys", [{
            "id": kid, "org_id": TEAM, "lookup_hash": lookup_hash(token),
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
        env["fake"].rpc("abuse_suspend", {"p_org_id": TEAM})
        with verdict_client(env["app"]) as tc:
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
        fake.rpc("abuse_suspend", {"p_org_id": TEAM})
        abuse.mark_suspended(TEAM)
        assert abuse.is_suspended_signal(TEAM)
        fake.missing_columns = {"organizations": {"suspended_at", "flagged_at"}}
        with verdict_client(env["app"]) as tc:
            r = tc.get("/v1/team", headers=_auth())
        assert r.status_code == 200  # degraded (accepted-by-scope)
        assert not abuse.is_suspended_signal(TEAM)  # self-heal tore it down
        assert fake.tables["organizations"][0]["suspended_at"] is not None  # durable stays

    def test_unsuspend_restores_next_request(self, env):
        fake = env["fake"]
        fake.rpc("abuse_suspend", {"p_org_id": TEAM})
        with verdict_client(env["app"]) as tc:
            assert tc.get("/v1/team/keys", headers=_auth()).status_code == 403
            fake.rpc("abuse_unsuspend", {"p_org_id": TEAM})
            assert tc.get("/v1/team/keys", headers=_auth()).status_code == 200

    def test_suspension_survives_restart(self, env):
        """Bypass failure mode: durable state rejects even with the process
        signal set cleared (simulated restart)."""
        fake = env["fake"]
        fake.rpc("abuse_suspend", {"p_org_id": TEAM})
        abuse.mark_suspended(TEAM)
        # simulate a worker restart: signal set + engine wiped
        with abuse._SIGNAL_LOCK:
            abuse._SUSPENDED_SIGNAL.clear()
        abuse.set_engine(None)
        with verdict_client(env["app"]) as tc:
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
        with verdict_client(env["app"]) as tc:
            for i in range(6):
                self._post_point(tc, i)
            org_row = fake.tables["organizations"][0]
            assert org_row["flagged_at"] is not None      # stage 1
            assert org_row["suspended_at"] is None        # never stage 2
            assert not abuse.is_suspended_signal(TEAM)
            for i in range(3):  # continued in-window writes: still no suspend
                self._post_point(tc, i)
            assert fake.tables["organizations"][0]["suspended_at"] is None
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
            assert fake.tables["organizations"][0]["suspended_at"] is None
        # The second burst is a NEW episode and must RE-FLAG and RE-ALERT — the
        # false-positive guarantee is that it never suspends. The alert budget
        # is released when the clean evaluation above ends the first episode
        # (#3631), so both episodes notify; assert both the durable re-flag and
        # the second alert.
        flag_rows = [e for e in fake.tables["abuse_events"]
                     if e["event_type"] == "flag"]
        assert len(flag_rows) >= 2
        assert [c[0] for c in env["notified"]].count("abuse_flag") >= 2

    def test_boundary_crossing_alerts_and_does_not_403(self, env):
        """A breach persisting past flagged_at + window escalates to a HUMAN
        review alert — NOT to a suspension. #5425 owner ruling: "we should just
        have rate-limits and if they want more they need to speak with us".

        The fake clock is compressed: age flagged_at 2h into the past while
        the window sum stays over threshold.
        """
        fake = env["fake"]
        with verdict_client(env["app"]) as tc:
            for i in range(6):
                self._post_point(tc, i)
            assert fake.tables["organizations"][0]["flagged_at"] is not None
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
            # #5425: stage 2 does NOT suspend, and the customer is not locked out.
            assert fake.tables["organizations"][0]["suspended_at"] is None
            assert tc.get("/v1/team/keys", headers=_auth()).status_code != 403
            # The escalation is a human-review alert — a dashboard row the
            # operator can see AND a notification, never an "auto-suspended" lie.
            assert [e for e in fake.tables["abuse_events"]
                    if e["event_type"] == "review_needed"]
            kinds = [c[0] for c in env["notified"]]
            assert "abuse_review_needed" in kinds
            assert "abuse_suspended" not in kinds
            # The 403 contract is UNCHANGED — it now requires a deliberate
            # OPERATOR suspend, which the engine no longer performs for anyone.
            fake.rpc("abuse_suspend", {"p_org_id": TEAM})
            assert tc.get("/v1/team/keys", headers=_auth()).status_code == 403

    def test_non_create_writes_never_trip_r1(self, env):
        """Delta 8: only Point CREATION records. REST has no point-update
        endpoint (purity by construction); the non-create write surface here
        is key revocation — revokes record NO point_create events. (MCP
        update/supersede/invalidate/retract purity is asserted by the
        source-introspection test below.)"""
        fake = env["fake"]
        fake.seed("api_keys", [{
            "id": "revoke-me", "org_id": TEAM,
            "lookup_hash": "revoke-hash", "key_prefix": "tt_revoke12",
            "created_via": "recovery", "created_by": "user-1",
            "created_at": "2026-08-01T00:00:00+00:00",
            "expires_at": None, "revoked_at": None,
        }])
        with verdict_client(env["app"]) as tc:
            for _ in range(6):
                r = tc.delete("/v1/team/keys/revoke-me", headers=_auth())
                assert r.status_code in (200, 404), r.text
        events = [e for e in fake.tables.get("abuse_events", [])
                  if e["event_type"] == "point_create"]
        assert events == []  # revokes never record point_create
        assert env["fake"].tables["organizations"][0]["flagged_at"] is None


# ── R2: key-create velocity ─────────────────────────────────────────────────

class TestKeyVelocity:
    def _provision(self, fake, lookup):
        fake.rpc("provision_team", {
            "p_user_id": None, "p_identity": f"id-{lookup[:8]}",
            "p_org_id": TEAM, "p_org_name": "abuse-team",
            "p_api_key": f"tt_{lookup}", "p_key_hash": "kh",
            "p_lookup_hash": lookup, "p_graph_name": f"org_{TEAM}",
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
        with verdict_client(env["app"]) as tc:
            r = tc.post("/v1/points", headers=_auth(),
                        json={"content": "next hooked request",
                              "kind": "statement"})
            assert r.status_code == 200
        assert fake.tables["organizations"][0]["flagged_at"] is not None
        flag_notifies = [c for c in env["notified"] if c[0] == "abuse_flag"]
        assert flag_notifies and flag_notifies[0][2]["rule"] == "key_create"

    def test_bootstrap_mints_excluded(self, env):
        """Delta 9: 11 bootstrap session mints produce zero key_create events
        and no flag (normal dashboard churn must not suspend)."""
        fake = env["fake"]
        for i in range(11):
            fake.query("api_keys", method="POST", json_body={
                "id": f"boot-{i}", "org_id": TEAM,
                "lookup_hash": f"boot-hash-{i}", "created_via": "bootstrap"})
        events = [e for e in fake.tables.get("abuse_events", [])
                  if e["event_type"] == "key_create"]
        assert events == []
        with verdict_client(env["app"]) as tc:
            r = tc.post("/v1/points", headers=_auth(),
                        json={"content": "hooked", "kind": "statement"})
            assert r.status_code == 200
        assert fake.tables["organizations"][0]["flagged_at"] is None


# ── R3: exfiltration detection ──────────────────────────────────────────────

class TestExfiltration:
    def test_per_key_velocity_notifies_once(self, env):
        with verdict_client(env["app"]) as tc:
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
        with verdict_client(env["app"]) as tc:
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
        with verdict_client(env["app"]) as tc:
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
        with verdict_client(env["app"]) as tc:
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
        with verdict_client(env["app"]) as tc:
            assert tc.get("/v1/team/keys", headers=_auth()).status_code == 200
        assert env["notified"] == []


# ── MCP transport ───────────────────────────────────────────────────────────

class TestSessionLane:
    """#1913: the session-JWT REST lane runs the same post-auth abuse
    evaluation as the key lanes (R3 read velocity + R4 geo). The key lanes
    call _abuse_post_auth (get_current_org / _get_current_team_supabase);
    _session_user_org — the session lane — never did: session-driven GETs
    from a new country recorded no auth_ip event and didn't count toward R3.
    """

    def _seed_membership(self, fake):
        fake.seed("org_memberships", [{
            "user_id": _U1, "org_id": TEAM, "role": "owner",
            "status": "active", "org_name": "abuse-team"}])

    def _ip_events(self, fake):
        return [e for e in fake.tables["abuse_events"]
                if e["event_type"] == "auth_ip"]

    def test_session_get_records_auth_ip_and_read(self, env):
        """A session-lane GET from a new country records an auth_ip event
        (R4) and counts toward R3 team read velocity."""
        from starlette.datastructures import Headers
        from starlette.requests import Request

        from tortoise.hosted_api import _session_user_org
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
        team = asyncio.run(_session_user_org(request, {"user_id": _U1}))
        assert team["org_id"] == TEAM
        # R4: the new-country session request recorded auth_ip + notified
        assert {e["country"] for e in self._ip_events(fake)} == {"MX"}
        geo = [c for c in env["notified"] if c[0] == "abuse_new_ip"]
        assert len(geo) == 1 and geo[0][2]["country"] == "MX"
        # R3: the session GET counted toward team read velocity
        assert len(abuse.READ_TRACKER._by_org[TEAM]) == 1

    def test_key_lane_unchanged(self, env):
        """Regression: the key lane still records auth_ip (R4) + R3 reads
        after the session-lane fix — behavior unchanged."""
        fake = env["fake"]
        with verdict_client(env["app"]) as tc:
            r = tc.get("/v1/team/keys",
                       headers={**_auth(), "CF-IPCountry": "CL"})
            assert r.status_code == 200
        assert {e["country"] for e in self._ip_events(fake)} == {"CL"}
        assert [c[0] for c in env["notified"]] == ["abuse_new_ip"]
        assert len(abuse.READ_TRACKER._by_org[TEAM]) == 1


class TestMcp:
    def _mcp_client(self):
        """Minimal Starlette app wrapped ONLY in TeamResolutionMiddleware —
        exercises the same resolution/suspension/geo path the /mcp mount uses."""
        from starlette.applications import Starlette  # noqa: I001
        from starlette.responses import JSONResponse
        from starlette.routing import Route
        from tortoise.mcp_auth import OrgResolutionMiddleware

        async def echo(request):
            await request.body()
            return JSONResponse({"ok": True})

        app = Starlette(routes=[Route("/", echo, methods=["POST"])])
        app.add_middleware(OrgResolutionMiddleware)
        return TestClient(app)

    def test_suspended_team_jsonrpc_error(self, env):
        env["fake"].rpc("abuse_suspend", {"p_org_id": TEAM})
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
            fake.rpc("abuse_suspend", {"p_org_id": TEAM})
            abuse.mark_suspended(TEAM)
            r = tc.post("/", headers=_auth(),
                        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            assert r.status_code == 403
            assert r.json()["error"]["code"] == -32006
            # un-suspend: durable clear + the next fresh resolution evicts
            fake.rpc("abuse_unsuspend", {"p_org_id": TEAM})
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
        fake.seed("org_memberships", [{
            "user_id": _U1, "org_id": TEAM, "role": "owner",
            "status": "active", "org_name": "abuse-team"}])
        fake.rpc("abuse_suspend", {"p_org_id": TEAM})
        env["app"].dependency_overrides[get_current_user] = \
            lambda: {"user_id": _U1}
        try:
            with verdict_client(env["app"]) as tc:
                r = tc.post("/v1/session/key",
                            json={"purpose": "recovery", "org_id": TEAM})
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
        fake.seed("org_memberships", [{
            "user_id": _U1, "org_id": TEAM, "role": "owner",
            "status": "active", "org_name": "abuse-team"}])
        # env fixture pre-seeds 2 provisioned keys at the free-tier cap
        # (max_api_keys=2). The DRIFT-phase mint has the suspension gate
        # bypassed (degrade) and would 402 at the cap before creating a
        # key — clear them so the 200 assertion holds. The recovery phase
        # 403s at the suspension gate before any cap check.
        # (precedent: test_auth_flip test_mint_resolves_then_revoked_rejected).
        fake.tables["api_keys"] = []
        fake.rpc("abuse_suspend", {"p_org_id": TEAM})
        fake.missing_columns = {"organizations": {"suspended_at", "flagged_at"}}
        env["app"].dependency_overrides[get_current_user] = \
            lambda: {"user_id": _U1}
        try:
            with verdict_client(env["app"]) as tc:
                r = tc.post("/v1/session/key",
                            json={"purpose": "recovery", "org_id": TEAM})
                assert r.status_code == 200  # fail-open during drift (accepted)
                assert "key" in r.json()  # the grant actually minted
            fake.missing_columns = None  # drift resolved
            with verdict_client(env["app"]) as tc:
                r = tc.post("/v1/session/key",
                            json={"purpose": "recovery", "org_id": TEAM})
            assert r.status_code == 403
            # Gate identity: the SUSPENDED gate specifically (not another 403).
            assert r.json()["detail"]["code"] == "SUSPENDED"
        finally:
            env["app"].dependency_overrides.clear()

    def test_alerts_endpoint_session_authed(self, env):
        from tortoise.hosted_api import get_current_user
        fake = env["fake"]
        fake.seed("org_memberships", [{
            "user_id": _U1, "org_id": TEAM, "role": "owner",
            "status": "active", "org_name": "abuse-team"}])
        store = SupabaseAbuseStore(fake)
        store.flag_org(TEAM, "point_create", {"count": 6})
        store.record_event(TEAM, "auth_ip", country="US")
        env["app"].dependency_overrides[get_current_user] = \
            lambda: {"user_id": _U1}
        try:
            with verdict_client(env["app"]) as tc:
                r = tc.get(f"/v1/team/alerts?org_id={TEAM}")
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
        fake.seed("org_memberships", [{
            "user_id": _U1, "org_id": TEAM, "role": "owner",
            "status": "active", "org_name": "abuse-team"}])
        SupabaseAbuseStore(fake).flag_org(TEAM, "point_create", {"count": 6})
        fake.rpc("abuse_suspend", {"p_org_id": TEAM})
        env["app"].dependency_overrides[get_current_user] = \
            lambda: {"user_id": _U1}
        try:
            with verdict_client(env["app"]) as tc:
                r = tc.get(f"/v1/team/alerts?org_id={TEAM}")
            assert r.status_code == 200
            assert any(a["type"] == "flag" for a in r.json()["alerts"])
        finally:
            env["app"].dependency_overrides.clear()

    def test_alerts_require_membership(self, env):
        from tortoise.hosted_api import get_current_user
        env["app"].dependency_overrides[get_current_user] = \
            lambda: {"user_id": _U_STRANGER}
        try:
            with verdict_client(env["app"]) as tc:
                assert tc.get(f"/v1/team/alerts?org_id={TEAM}").status_code == 403
        finally:
            env["app"].dependency_overrides.clear()

    def test_team_info_status_flagged(self, env):
        """status ∈ {active, flagged} over HTTP — suspension 403s earlier."""
        fake = env["fake"]
        fake.tables["organizations"][0]["flagged_at"] = \
            datetime.now(timezone.utc).isoformat()  # noqa: UP017
        with verdict_client(env["app"]) as tc:
            r = tc.get("/v1/team", headers=_auth())
        assert r.status_code == 200
        assert r.json()["status"] == "flagged"


# ── #10156: registers, episodes, acknowledgement ───────────────────────────

class TestAlertRegistersAndAcks:
    """#10156: the /v1/team/alerts contract the dashboard's two registers read.

    The customer saw a month-old, never-enforced read-velocity event rendered as
    "Security alerts — Suspicious activity detected … Revoke any key you don't
    recognize". The fix splits the feed by register, folds each sustained bout
    into one row with a count, and lets an owner/admin acknowledge an episode so
    it stops rendering as current. All of it travels on this endpoint: the panel
    cannot invent a register, a count or an acknowledgement of its own.
    """

    def _seed_velocity(self, fake, org_id=TEAM, key="k-10156"):
        """Seed the table directly, not through ``record_event``.

        ``SupabaseAbuseStore.record_event`` does not transmit ``created_at``
        (the column's ``DEFAULT now()`` owns it, deliberately — a writer clock
        must not be able to backdate an abuse row), so a fixture that wants
        specific timestamps has to write the rows the way the DB produces them.
        """
        # The 13 real windows of the 2026-09-25/26 bout: one CONTINUOUS bout,
        # which is the case #10156 is about (a 3-row sample would leave 6h
        # silences between rows and legitimately fold into 3 episodes).
        windows = ("2026-09-25T17:19:05+00:00", "2026-09-25T17:24:06+00:00",
                   "2026-09-25T18:57:00+00:00", "2026-09-25T19:35:02+00:00",
                   "2026-09-25T21:05:03+00:00", "2026-09-25T21:10:04+00:00",
                   "2026-09-25T21:34:03+00:00", "2026-09-25T21:39:04+00:00",
                   "2026-09-25T21:44:04+00:00", "2026-09-25T23:53:55+00:00",
                   "2026-09-25T23:58:55+00:00", "2026-09-26T00:03:55+00:00",
                   "2026-09-26T00:08:55+00:00")
        store = SupabaseAbuseStore(fake)
        fake.seed("abuse_events", [
            {"id": i + 1, "org_id": org_id, "event_type": "read_velocity",
             "details": {"scope": "key", "id": key}, "created_at": iso}
            for i, iso in enumerate(windows)])
        return store

    def _owner(self, env, role="owner"):
        from tortoise.hosted_api import get_current_user
        env["fake"].seed("org_memberships", [{
            "user_id": _U1, "org_id": TEAM, "role": role,
            "status": "active", "org_name": "abuse-team"}])
        env["app"].dependency_overrides[get_current_user] = \
            lambda: {"user_id": _U1}

    def test_the_feed_carries_the_register_and_the_episode(self, env):
        self._owner(env)
        self._seed_velocity(env["fake"])
        try:
            with verdict_client(env["app"]) as tc:
                r = tc.get(f"/v1/team/alerts?org_id={TEAM}")
            assert r.status_code == 200
            alerts = r.json()["alerts"]
            assert len(alerts) == 1, alerts
            row = alerts[0]
            assert row["type"] == "read_velocity"
            assert row["register"] == "activity", row
            assert row["count"] == 13, row
            assert row["first_at"].startswith("2026-09-25T17:19:05")
            assert row["acked"] is False
            assert row["episode"], row
            assert "_subject" not in row, "an internal grouping field leaked"
        finally:
            env["app"].dependency_overrides.clear()

    def test_a_FAILED_saturation_probe_cannot_take_the_alert_feed_with_it(
            self, env, monkeypatch):
        """P1 (round-23 review). The probe shared the route's `try` with the read
        that produces `alerts`, so a probe failure (a store without the method, a
        transient error) discarded an already-successful read and returned an
        empty feed — the panel unmounts on empty, so a live `suspend` row
        disappeared from the dashboard. A SECONDARY read must never destroy the
        PRIMARY result."""
        self._owner(env)
        self._seed_velocity(env["fake"])
        from tortoise import supabase_control

        class _ProbeBlind:
            """A real store that fails ONLY its own saturation probe."""

            def __init__(self, fake):
                from tortoise.abuse import SupabaseAbuseStore
                self._store = SupabaseAbuseStore(fake)

            def __getattr__(self, name):
                return getattr(self._store, name)

            def alert_scan_saturated(self, org_id):
                raise RuntimeError("probe failed")

        monkeypatch.setattr(supabase_control, "get_abuse_store",
                            lambda *a, **k: _ProbeBlind(env["fake"]))
        try:
            with verdict_client(env["app"]) as tc:
                r = tc.get(f"/v1/team/alerts?org_id={TEAM}")
            assert r.status_code == 200, r.text
            body = r.json()
            assert [a["type"] for a in body["alerts"]] == ["read_velocity"], (
                "a failed saturation probe emptied the alert feed — a secondary "
                "read took the primary result with it")
            assert body["scan_saturated"] is True, (
                "an unreadable probe must fail toward 'the window may be cut', "
                "never toward 'this feed is complete'")
            assert body["unavailable"] is False
        finally:
            env["app"].dependency_overrides.clear()

    def test_an_UNREADABLE_feed_is_reported_as_unavailable_not_as_empty(
            self, env, monkeypatch):
        """P2 (round-23 review). `alerts: []` is the all-clear: the panel unmounts
        and the customer sees no security section at all, which is
        indistinguishable from "we looked and found nothing" — on the one screen
        whose job is not to hide incidents. "We could not look" is a different
        fact and travels as its own flag."""
        self._owner(env)
        from tortoise import supabase_control

        class _Down:
            def recent_alerts(self, *a, **k):
                raise RuntimeError("control plane down")

        monkeypatch.setattr(supabase_control, "get_abuse_store",
                            lambda *a, **k: _Down())
        try:
            with verdict_client(env["app"]) as tc:
                r = tc.get(f"/v1/team/alerts?org_id={TEAM}")
            assert r.status_code == 200, (
                "an alert-history failure must not 500 the dashboard")
            body = r.json()
            assert body["alerts"] == []
            assert body["unavailable"] is True, (
                "a failed read reported itself as an empty feed — the dashboard "
                "renders that as an all-clear")
        finally:
            env["app"].dependency_overrides.clear()

    def test_the_acknowledgement_persists_and_the_row_stops_being_current(self, env):
        self._owner(env)
        store = self._seed_velocity(env["fake"])
        try:
            with verdict_client(env["app"]) as tc:
                episode = tc.get(f"/v1/team/alerts?org_id={TEAM}").json()[
                    "alerts"][0]["episode"]
                r = tc.post("/v1/team/alerts/ack",
                            json={"org_id": TEAM, "episode": episode})
                assert r.status_code == 200, r.text
                assert r.json()["acknowledged"] is True
                again = tc.get(f"/v1/team/alerts?org_id={TEAM}").json()
            assert again["alerts"][0]["acked"] is True
            # The DURABLE evidence: a row, not a cookie or a process cache.
            ack_rows = [x for x in env["fake"].tables["abuse_events"]
                        if x["event_type"] == "alert_ack"]
            assert len(ack_rows) == 1
            assert ack_rows[0]["details"]["episode"] == episode
            # …and the ack row is not itself an alert in the feed.
            assert store.recent_alerts(TEAM)[0]["type"] == "read_velocity"
        finally:
            env["app"].dependency_overrides.clear()

    def test_a_second_INCIDENT_behind_a_stale_render_is_refused_not_absorbed(self, env):
        """P2 (round 9 review). The ack's window is the episode's newest row AT
        WRITE TIME, and the incident gate only refuses rows newer than THAT — so
        anything arriving between the render and the click was folded into the
        dismissal and could never re-open it. A re-suspension inside the episode
        gap is exactly that case, and a suspension writes no follow-up row, so
        nothing else would ever surface it: the panel would print "Everything
        shown here is acknowledged." over a live second suspension.

        The client now sends the `at` it rendered, as an ASSERTION only (the
        window stays server-derived). If our feed has moved past it, refuse."""
        self._owner(env)
        env["fake"].seed("abuse_events", [
            {"id": 1, "org_id": TEAM, "event_type": "suspend", "details": {},
             "created_at": "2026-09-25T17:19:05+00:00"}])
        try:
            with verdict_client(env["app"]) as tc:
                rendered = tc.get(f"/v1/team/alerts?org_id={TEAM}").json()[
                    "alerts"][0]
                # …and then the org is suspended AGAIN, inside the episode gap,
                # before the owner clicks. A second row of the same identity.
                env["fake"].seed("abuse_events", [
                    {"id": 2, "org_id": TEAM, "event_type": "suspend",
                     "details": {},
                     "created_at": "2026-09-25T17:41:05+00:00"}])
                r = tc.post("/v1/team/alerts/ack",
                            json={"org_id": TEAM,
                                  "episode": rendered["episode"],
                                  "rendered_at": rendered["at"]})
                assert r.status_code == 409, (r.status_code, r.text)
                assert not [x for x in env["fake"].tables["abuse_events"]
                            if x["event_type"] == "alert_ack"], (
                    "the stale click wrote a dismissal that covers the second "
                    "suspension")
                # The CURRENT render acknowledges fine — the control still works,
                # it just has to answer what is on screen now.
                fresh = tc.get(f"/v1/team/alerts?org_id={TEAM}").json()["alerts"][0]
                assert fresh["count"] == 2, fresh
                r2 = tc.post("/v1/team/alerts/ack",
                             json={"org_id": TEAM, "episode": fresh["episode"],
                                   "rendered_at": fresh["at"]})
                assert r2.status_code == 200, r2.text
        finally:
            env["app"].dependency_overrides.clear()

    def test_an_OBSERVATION_does_not_livelock_its_own_dismissal(self, env):
        """The refusal above is INCIDENT-only, deliberately. Dismissing an ongoing
        RATE must keep working while new windows arrive — that is the whole point
        of `_episode_acknowledged` absorbing post-ack observations, and refusing
        them would mean a click during a burst bounces until the burst stops."""
        self._owner(env)
        env["fake"].seed("abuse_events", [
            {"id": 1, "org_id": TEAM, "event_type": "read_velocity",
             "details": {"scope": "key", "id": "k-1"},
             "created_at": "2026-09-25T17:19:05+00:00"}])
        try:
            with verdict_client(env["app"]) as tc:
                rendered = tc.get(f"/v1/team/alerts?org_id={TEAM}").json()[
                    "alerts"][0]
                env["fake"].seed("abuse_events", [
                    {"id": 2, "org_id": TEAM, "event_type": "read_velocity",
                     "details": {"scope": "key", "id": "k-1"},
                     "created_at": "2026-09-25T17:24:06+00:00"}])
                r = tc.post("/v1/team/alerts/ack",
                            json={"org_id": TEAM,
                                  "episode": rendered["episode"],
                                  "rendered_at": rendered["at"]})
                assert r.status_code == 200, (r.status_code, r.text)
        finally:
            env["app"].dependency_overrides.clear()

    #: A HUMAN ACTOR — the one closed thing a customer string may not name,
    #: because delivery of any operator signal is BEST-EFFORT: the row cannot know
    #: that anyone was asked, let alone what they did. Rounds 14-17 walked to this
    #: from the wrong end, enumerating VERBS beside the actor, and a verb set is
    #: open: "An operator reviewed this." (past tense), "Our team may review this."
    #: (a modal nobody listed) and "An operator took a look at it." (an irregular
    #: past `\w+ed` cannot match) each shipped green. The role noun is a small
    #: closed vocabulary — and it is OUR vocabulary for OUR staff, so a string
    #: naming one of these words is making a claim about us, whatever grammar
    #: follows it.
    #:
    #: Deliberately NOT here: `us`. "contact us" / "reach out to us" is the
    #: customer's own action, and "us" cannot be the subject of a claim
    #: ("us will review" is not English) — the elided-actor promise list below
    #: covers "hear from us".
    #: Split for a reason (round 18 finding): the second group are AMBIGUOUS
    #: common nouns that a legitimate customer-directed string can use — "Review
    #: your security settings." names the CUSTOMER's settings, not a claim about
    #: us — so they are matched only when they are not customer-determined. The
    #: first group are our staff and ours alone; a string naming one of those is
    #: making a claim about us whatever determiner precedes it.
    #: Round 19 (review finding): the customer-determined exemption was a
    #: lookbehind on the noun, which only sees ADJACENCY — "Ask your security team
    #: to revoke it." suppressed `security` and then matched `team`, whose
    #: preceding characters are "rity ". A lookbehind cannot span the phrase, so
    #: the phrase is REMOVED before the match instead (see `_claims`), exactly like
    #: the address-as-object strip.
    _ROLE = (r"(?:(?:an?|the|our)\s+)?(?:operators?|humans?|engineers?|"
             r"responders?|developers?|analysts?|sres?|support(?:ers?)?)"
             r"|(?:(?:an?|the|our)\s+)?(?:staff|desks?|security|teams?)")
    #: First person is a special case: `we` in any tense is a claim about us, and
    #: the only legitimate first-person string this panel ships is an imperative
    #: that names the customer ("contact support@…"), not us. `\bwe\b` also
    #: covers the contractions ("We're", "We’ve") — `'` and `’` are not word
    #: characters, so the boundary is between "e" and the apostrophe — which is
    #: why there is no separate contraction arm (round 18: there was one, and it
    #: had no discriminating power; a dead arm in a pin reads as coverage).
    _HUMAN = re.compile(
        rf"(?:\b(?:{_ROLE})\b|\bwe\b|\bby\s+us\b)", re.I)
    #: A promise of contact/timing where the actor is ELIDED ("…, and will reach
    #: out"), which the actor arms cannot see.
    _PROMISE = re.compile(
        r"(will be in touch|(?:will|'ll)\s+reach(?:ing)?\s+out|"
        r"get back to you|follow(?:ing)? up with you|hear from us|"
        r"within \d+\s*(?:hours?|days?|business days?))", re.I)
    #: BOTH `scope` branches (the org-total string was outside the loop until
    #: round 12), plus the empty-details case. Also fed to the accept fixture so
    #: it cannot drift from what actually ships.
    @staticmethod
    def _detail_variants() -> tuple[dict, ...]:
        return ({"scope": "key", "id": "k-1", "count": 3, "ip": "1.2.3.4",
                 "country": "ES"},
                {"scope": "team", "id": "org-1", "count": 3},
                {})

    #: `auth_ip`'s country is a ROW COLUMN, not a `details` key (``recent_alerts``
    #: selects it, ``check_new_country`` only fires with one), so a loop that only
    #: varies `details` never renders "…: ES" — round-15 gate finding: that string
    #: was pinned only as a hand-typed literal, which would go stale silently if
    #: the clause's format changed. Both column states are rendered here.
    @staticmethod
    def _row_variants() -> tuple[dict, ...]:
        return ({}, {"country": "ES"}, {"country": None})

    @staticmethod
    def _claims(text: str, etype: str | None = None) -> bool:
        """The shipped predicate: the REMOVALS below, then the actor and promise arms.

        Everything a customer string legitimately contains that LOOKS like a human
        claim is removed before the match — and this list is the whole suppression
        surface, so read it before widening any of them (round 20 finding: it
        lived above `_detail_variants` and said "two", so the enumeration of what
        can HIDE a match was both miscounted and filed on the wrong method):

          * an email address in OBJECT position ("contact support@…" addresses the
            support DESK; it does not report what support did);
          * the CUSTOMER's own staff/settings ("your security team"), which are not
            a claim about ours — with the window forbidden to cross one of OUR
            words, so the strip cannot erase an actor;
          * `suspend`'s "by an operator", backed by the row's EXISTENCE:
            `suspend_org()` is the only writer of EVENT_SUSPEND and the engine no
            longer suspends, so the row IS the proof.

        A removal is where a pin hides a hole, so each is pinned in both
        directions below.
        """
        # Round 18 (review finding): stripping EVERY address deleted the SUBJECT
        # of a sentence too ("You'll hear from support@…", "Support@… has been
        # notified"), so an actor named by address sailed through. Only an
        # ADDRESS AS OBJECT of a contact imperative is a legitimate match-free
        # zone — "contact support@…" tells the customer where to write, it does
        # not report what support did.
        stripped = re.sub(
            r"(?i)\b(?:contact|email|e-mail|reach(?:\s+out)?\s+to|write\s+to"
            r"|message)\s+\S+@\S+", "", text)
        # The CUSTOMER's own staff/settings are not a claim about ours, and the
        # phrase can be several words long ("your security team", "your own
        # desk"), which a fixed-width lookbehind cannot express.
        # Round 20 (review finding): an UNCONSTRAINED window let the strip span a
        # FIRST-GROUP word and complete on a trailing ambiguous noun, so
        # "Your support team has been notified." was reduced to " has been
        # notified." and shipped green — our own staff, named, deleted by a strip
        # meant to protect the customer's. The window now refuses to cross one.
        stripped = re.sub(
            r"(?i)\byour(?:\s+own)?"
            r"(?:\s+(?!(?:operators?|humans?|engineers?|responders?|"
            r"developers?|analysts?|sres?|support(?:ers?)?)\b)\w+){0,2}\s+"
            r"(?:staff|desks?|security|teams?|settings)\b", "", stripped)
        # The `suspend` exemption, and ONLY for that row type. `suspend_org()`
        # writing the row proves an operator suspended the org — not what an
        # operator did next, and not anything about any OTHER event type.
        # Round 16: the unanchored version deleted the phrase ANYWHERE and
        # laundered a continued predicate; round 17: keyed on the text alone it
        # would also bless "Key revoked by an operator." on any other row, so it
        # is now (a) clause-final and (b) scoped to `suspend`.
        if etype == "suspend":
            # Bound to the PREDICATE, not just the position (round 18 finding):
            # "Organization suspended. Your API key was revoked by an operator."
            # is also clause-final, and stripping it there blessed a claim about
            # a different action entirely. The row proves the SUSPENSION.
            stripped = re.sub(
                r"(?i)\bsuspended\s+by an operator\b(?=\s*[.;!?]|\s*$)",
                "suspended", stripped)
        return bool(TestAlertRegistersAndAcks._HUMAN.search(stripped)
                    or TestAlertRegistersAndAcks._PROMISE.search(stripped))

    def test_the_human_claim_predicate_actually_REJECTS_known_bad_strings(self):
        """The discriminating-power fixture (round 12 review finding).

        Six rounds of this test failed the same way: the stated guarantee was
        wider than the assertion, so it could not fail for the reason it exists —
        round 9 pinned a phrase, round 10 enumerated phrasings, round 11 fixed the
        word ORDER but not the direction, round 14 enumerated VERBS while claiming
        the verb was irrelevant, round 15 enumerated AUXILIARIES (a past tense and
        an irregular past escaped), and round 16 found the arms were not arms at
        all: an unparenthesised `_ACTOR` alternation made `\bwe\b` a standalone
        alternative. The root cause each time was enumerating an OPEN set. The pin
        now names a CLOSED one — the product's own vocabulary for its staff, which
        no customer string may reference — and this fixture holds strings across
        present, past, passive, modal, irregular-past and contraction grammar,
        plus every shipped label and message, so the next round has to demonstrate
        discriminating power rather than assert it. (No counts in this docstring:
        a number here re-stales the moment a fixture entry is added, which is the
        same defect as a comment that asserts an invariant nothing checks.)

        STATED RESIDUAL RISK — read this before trusting the pin further than it
        goes. It is a FLOOR, not a proof, and the shapes still passing are named
        WITHOUT a count (a count here re-stales the moment a shape is found, which
        is how this paragraph shipped saying "two" while the code allowed three):
        an ACTOR-ELIDED passive ("Your keys have been reviewed."), a noun outside
        the staff vocabulary ("A specialist reviewed your account."), and an
        ELIDED-ACTOR promise phrased outside `_PROMISE` ("Expect a reply
        shortly."). Each is a claim the row cannot know.

        There is no grammar here that could catch them, so the ONE rule for a
        future copy change is: extend `_ROLE` for a new word naming our staff,
        extend `_PROMISE` for a new way of promising contact or timing — in the
        same commit. (Round 18 closed shapes that had been hiding INSIDE the
        exemptions — an address in subject position, a second clause after "by an
        operator", "by us" as a passive agent — which is why each exemption is
        pinned in both directions rather than trusted.)

        The error direction is deliberate: an unlisted benign phrase that NAMES our
        staff (a bare "contact support.") is REJECTED and reddens the shipped-copy
        assertion loudly, while an unlisted false claim passes quietly. Loud beats
        quiet, which is why the vocabulary is enumerated rather than the claims
        being allowed."""
        #: Any string naming a member of OUR staff (or claiming first-person
        #: action) is a claim we cannot back. The list below is deliberately
        #: diverse in GRAMMAR — present, past, passive, modal, irregular past,
        #: contraction, actor-last — because the point of the round-17 rewrite is
        #: that grammar no longer decides: the NOUN does. Each entry names a role.
        must_reject_human = [
            "Nothing has been disabled, and our team is reviewing it.",
            "…and an operator has been asked to look at it.",
            "This alert was escalated to an operator for review.",
            "We're investigating this now.",
            "We\u2019re investigating this now.",  # typographic apostrophe
            "An operator is looking into it.",
            "An operator will review this.",
            "Our on-call engineer was paged.",
            "Our engineers have been notified.",
            "Our SRE will take a look.",
            "Our staff is aware.",
            "This alert has already been escalated to our team.",
            # PAST TENSE — an auxiliary enumeration could not see these
            "An operator reviewed this.",
            "Our team looked into it.",
            "The security team is investigating.",
            "An incident responder has been paged.",
            # A MODAL nobody enumerated, and an IRREGULAR past `\w+ed` misses
            "Our team may review this.",
            "An operator took a look at it.",
            # OUR OWN STAFF WORD — the vocabulary this panel uses for support
            "Support has been notified.",
            "The support desk has been notified.",
            "Team will review this.",
            # round 18: an address in SUBJECT position names the actor
            "You'll hear from support@premiselabs.co.",
            "Support@premiselabs.co has been notified.",
            # round 18: the passive by-agent form of the first person
            "Your keys will be reviewed by us.",
            # round 20: the customer-determined strip must not CROSS one of OUR
            # words to reach an ambiguous noun — "your" cannot launder an actor
            # that is in the staff vocabulary. Deliberately the LOUD direction:
            # "your support team" may well be the customer's own, and it is
            # rejected anyway, because the vocabulary is what the row can prove.
            "Your support team has been notified.",
            "Your support desk has been notified.",
            "Your responders team reviewed this.",
            "Your analysts desk replied.",
            "Your SRE desk was paged.",
        ]
        #: Caught by the PROMISE list where the actor is ELIDED, which no actor
        #: arm can see. Each must NOT match the actor pattern, or it proves
        #: nothing about the promise list.
        must_reject_promise = [
            "Nothing has been disabled, and will be in touch.",
            "Expect a response within 24 hours.",
            "…and will reach out shortly.",
            # "us" is allowed as an OBJECT (it cannot be the subject of a claim),
            # so a timeline promise built on it is the promise list's job.
            "You will hear from us within 24 hours.",
        ]
        for text in must_reject_human:
            assert self._HUMAN.search(text), (
                f"the actor pattern misses a false human claim: {text!r} — a "
                "pin that cannot reject this cannot guard the real copy either")
            # …AND the shipped PREDICATE, which the arms alone cannot prove.
            # Round 20 (review finding): these loops only ever exercised
            # `_HUMAN`/`_PROMISE`, so a stripping step that DELETED a named actor
            # could widen freely without reddening the "discriminating power"
            # fixture — which is how the customer-determined strip came to erase
            # "Your support team". A fixture for the predicate has to call it.
            assert self._claims(text), (
                f"the PREDICATE accepts a false human claim: {text!r} — an arm "
                "matched it, so a pre-strip is deleting the actor")
        for text in must_reject_promise:
            assert self._PROMISE.search(text), (
                f"the PROMISE list misses an elided-actor promise: {text!r}")
            assert self._claims(text), (
                f"the PREDICATE accepts an elided-actor promise: {text!r}")
            assert not self._HUMAN.search(text), (
                f"{text!r} is supposed to prove the PROMISE list (actor "
                "elided) but the actor pattern catches it too, so the promise "
                "arm stays unproven for this shape")
        # ``suspend``'s exemption, proven in BOTH directions rather than asserted:
        # a clause-final "by an operator" on a `suspend` row is backed by the row's
        # existence, while the SAME phrase on any other row, or in a second
        # clause, is not. (No count here: the surface is `_claims`' docstring.)
        assert not self._claims("Organization suspended by an operator",
                                "suspend"), (
            "the exemption stopped covering the one string it exists for")
        assert self._claims("Key revoked by an operator.", "flag"), (
            "the suspend exemption leaked onto another event type")
        assert self._claims(
            "Organization suspended by an operator after reviewing your "
            "request.", "suspend"), (
            "the exemption laundered a continued predicate")
        assert self._claims(
            "Organization suspended. Your API key was revoked by an operator.",
            "suspend"), (
            "the exemption blessed a SECOND clause: it must be bound to the "
            "suspension predicate, not merely to a clause-final position")
        # …and legitimate copy must still pass, including every SHIPPED string.
        #: (text, etype) pairs, because the `suspend` exemption is per-ROW: the
        #: same sentence on another type is a claim, so the type must travel with
        #: the text through the check.
        must_accept = [
            "review your API keys in Keys — an unrecognised key should be "
            "revoked there",
            "If you need a higher limit, contact support@premiselabs.co.",
            "If you need a higher limit, reach out to support@premiselabs.co.",
            # an imperative that names the customer, and asks them to reach US
            "If this was not you, contact us.",
            # the CUSTOMER's own team/settings are not a claim about our staff —
            # including the PHRASE forms, which the old adjacent-only guard
            # rejected (round 19 finding)
            "Review your security settings.",
            "Manage your team in Settings.",
            "Ask your security team to revoke the key.",
            "Ask your own security desk to revoke the key.",
            # …and the same phrase form where the whole phrase is
            # CUSTOMER-DETERMINED. Note the reason carefully: `engineering` is
            # outside the staff vocabulary but `team` and `security` are INSIDE
            # its second group, so what keeps these green is the `your` strip,
            # NOT the vocabulary — narrowing the strip's final-noun group would
            # reject them.
            "Your engineering team reviewed this.",
            "Your security team reviewed this.",
            "Suspicious activity flagged on this Organization",
            "Access from a new location: ES",
            "Access from a new location",
        ]
        accept_pairs = [(t, None) for t in must_accept]
        # Add every SHIPPED message + label for every type and detail variant,
        # so the accept half can never drift from the product.
        for etype in ALERT_TYPES:
            for details in self._detail_variants():
                for extra in self._row_variants():
                    row = _alert_dict({"event_type": etype, "details": details,
                                       "created_at": "2026-09-25T17:19:05+00:00",
                                       **extra})
                    accept_pairs.append((str(row.get("label") or ""), etype))
                    accept_pairs.append((str(row.get("message") or ""), etype))
        for text, etype in accept_pairs:
            if not text:
                continue
            assert not self._claims(text, etype), (
                f"the predicate rejects SHIPPED/accepted copy: {text!r} "
                f"(type {etype})")

    def test_no_customer_string_claims_what_a_HUMAN_is_doing(self):
        """Rounds 6 / 9 / 10 / 11 / 12 (review findings). The same promise was
        deleted five times: "and will be in touch" → "our team is reviewing it" →
        "an operator has been asked to look at it" → (a whole-state reassurance)
        → (a benign-cause assertion). Each rewrite was weaker and each was still a
        claim the plumbing cannot back: delivery of the operator signal is
        BEST-EFFORT, so in the configurations this module names (no
        ``DR_ISSUES_PAT``, no usable object store) no operator was ever asked.

        This asserts the PROPERTY over every rendered field of every alert type,
        and BOTH branches of ``_read_velocity_message`` — the org-total branch is
        a separate string and was outside the loop until round 12, i.e. one of the
        two strings of the type this issue is about was unasserted. The removals
        `_claims` performs are listed in ITS docstring and nowhere else, because a
        suppression surface described in two places drifts — which is what
        happened here (round 21 review finding: this paragraph named a COUNT of
        documented exclusions that was already wrong, and described an
        "imperative addresses the customer" rule the code does not implement: a
        bare "contact support." naming our desk IS rejected, and "contact us"
        passes only because `us` is not an actor arm). What makes
        ``suspend``'s "by an operator" safe is that it binds no auxiliary and the
        row's existence is the proof; ``suspend``'s exemption, being the one with
        a condition attached, is pinned in both directions below."""
        detail_variants = self._detail_variants()
        for etype in ALERT_TYPES:
            for details in detail_variants:
              for extra in self._row_variants():
                row = _alert_dict({"event_type": etype, "details": details,
                                   "created_at": "2026-09-25T17:19:05+00:00",
                                   **extra})
                for field in ("label", "message"):
                    text = str(row.get(field) or "")
                    # `_claims` applies every removal it lists (in its own
                    # docstring — the list lives in ONE place) then runs BOTH
                    # mechanisms.
                    assert not self._claims(text, etype), (
                        f"{etype}.{field} asserts a human or a follow-up: "
                        f"{text!r} — the operator signal is best-effort, so we "
                        "cannot know")
        # The removals restated against the REAL strings, in the direction that
        # makes each safe (the inventory itself is `_claims`' docstring).
        suspended = _alert_dict({"event_type": "suspend", "details": {},
                                 "created_at": "2026-09-25T17:19:05+00:00"})
        assert "by an operator" in suspended["message"], suspended["message"]
        assert not self._claims(suspended["message"], "suspend")
        velocity = _alert_dict({"event_type": "read_velocity",
                                "details": {"scope": "key"},
                                "created_at": "2026-09-25T17:19:05+00:00"})
        assert "review your API keys" in velocity["message"], velocity["message"]
        assert not self._claims(velocity["message"])
        # …and the review row still says what it CAN know: what THIS alert did.
        msg = _alert_dict({"event_type": "review_needed", "details": {},
                           "created_at": "2026-09-25T17:19:05+00:00"})["message"]
        assert "It did not disable anything" in msg, msg
        assert "support@premiselabs.co" in msg, msg
        # Every RATE detector shares ONE shape (round-14 finding: recovery_velocity
        # was the only one without the informational clause and read as an
        # incident). Pinning the SHAPE rather than one type's phrase is what makes
        # a new rate type fail here instead of shipping with a bare "detected".
        for etype, register in abuse.ALERT_REGISTER_OF.items():
            if register != abuse.REGISTER_ACTIVITY:
                continue
            m = str(_alert_dict({"event_type": etype, "details": {},
                                 "created_at": "2026-09-25T17:19:05+00:00"}
                                ).get("label") or "")
            body = str(_alert_dict({"event_type": etype, "details": {},
                                    "created_at": "2026-09-25T17:19:05+00:00"}
                                   ).get("message") or "")
            # A rate is a measurement, so the LABEL must not be an incident word
            # and the MESSAGE must say nothing changed and offer a route.
            assert not re.search(r"(suspicious|incident|compromis|breach|"
                                 r"attack)", m, re.I), (etype, m)
            assert "did not change anything" in body, (etype, body)
            assert ("keys" in body.lower() or "support@" in body.lower()), (
                etype, body)
        # No WHOLE-STATE claim anywhere: the same panel can carry a suspension
        # row, so "nothing has been disabled" would contradict it.
        for etype in ALERT_TYPES:
            for details in detail_variants:
                m = str(_alert_dict({"event_type": etype, "details": details,
                                     "created_at": "2026-09-25T17:19:05+00:00",
                                     "country": "ES"}
                                    ).get("message") or "")
                assert "nothing has been disabled" not in m.lower(), (etype, m)
                assert "nothing was changed" not in m.lower(), (etype, m)
                assert "nothing has changed" not in m.lower(), (etype, m)

    def test_only_owner_or_admin_may_acknowledge(self, env):
        """An ack hides a security row from the whole org — that is a
        management act, so a plain member cannot perform it."""
        self._owner(env, role="member")
        self._seed_velocity(env["fake"])
        try:
            with verdict_client(env["app"]) as tc:
                r = tc.post("/v1/team/alerts/ack",
                            json={"org_id": TEAM, "episode": "x"})
            assert r.status_code == 403
            assert not [x for x in env["fake"].tables["abuse_events"]
                        if x["event_type"] == "alert_ack"]
        finally:
            env["app"].dependency_overrides.clear()

    def test_ack_requires_the_episode(self, env):
        self._owner(env)
        try:
            with verdict_client(env["app"]) as tc:
                assert tc.post("/v1/team/alerts/ack",
                               json={"org_id": TEAM}).status_code == 422
                assert tc.post("/v1/team/alerts/ack",
                               json={}).status_code == 422
        finally:
            env["app"].dependency_overrides.clear()

    def test_ack_stays_reachable_while_suspended(self, env):
        """Delta 12 parity: the APPEAL flow must keep working on a suspended
        org, and that surface is the same one the ack lives on. Acknowledging
        changes no enforcement state, so it must not acquire a suspension gate
        just because its neighbours (key management) have one."""
        self._owner(env)
        self._seed_velocity(env["fake"])
        env["fake"].rpc("abuse_suspend", {"p_org_id": TEAM})
        try:
            with verdict_client(env["app"]) as tc:
                episode = tc.get(f"/v1/team/alerts?org_id={TEAM}").json()[
                    "alerts"][0]["episode"]
                r = tc.post("/v1/team/alerts/ack",
                            json={"org_id": TEAM, "episode": episode})
            assert r.status_code == 200, r.text
        finally:
            env["app"].dependency_overrides.clear()

    def test_ack_refuses_an_episode_that_is_not_in_the_feed(self, env):
        """#10156 review finding: the route accepted ANY string from an owner and
        wrote a row nothing would ever match — accumulating one dead row per
        attempt. It now resolves the episode against the org's own feed."""
        self._owner(env)
        self._seed_velocity(env["fake"])
        try:
            with verdict_client(env["app"]) as tc:
                r = tc.post("/v1/team/alerts/ack",
                            json={"org_id": TEAM, "episode": "made-up|x|y"})
            assert r.status_code == 404, r.text
            assert not [x for x in env["fake"].tables["abuse_events"]
                        if x["event_type"] == "alert_ack"]
        finally:
            env["app"].dependency_overrides.clear()

    def test_a_TRUNCATED_episode_can_be_acknowledged_and_the_ack_survives(self, env):
        """P1 (round-3 review), the shape that costs a customer most.

        A truncated episode's KEY is anchored to the oldest row the scan can see,
        so it moves as the window slides. An intermediate version refused those
        with 409 — which made the oldest, longest-running bout PERMANENTLY
        undismissable (a current security row with no way to clear it). The ack
        now records the WINDOW it saw and is matched by OVERLAP, so it survives
        the scan sliding past the episode's start."""
        self._owner(env)
        fake = env["fake"]
        base = datetime(2026, 9, 25, 17, 19, tzinfo=timezone.utc)  # noqa: UP017
        # One row past the scan, all one key: the oldest episode is cut.
        fake.seed("abuse_events", [
            {"id": i + 1, "org_id": TEAM, "event_type": "read_velocity",
             "details": {"scope": "key", "id": "k-10156"},
             "created_at": (base + timedelta(minutes=5 * i)).isoformat()}
            for i in range(abuse._ALERT_SCAN + 1)])
        try:
            with verdict_client(env["app"]) as tc:
                feed = tc.get(f"/v1/team/alerts?org_id={TEAM}").json()["alerts"]
                assert feed and feed[-1]["truncated"] is True, feed[-1]
                r = tc.post("/v1/team/alerts/ack",
                            json={"org_id": TEAM,
                                  "episode": feed[-1]["episode"]})
                assert r.status_code == 200, r.text
                # The window we saw was recorded, from OUR feed — not from the
                # request (the client only ever sends the key).
                ack = [x for x in fake.tables["abuse_events"]
                       if x["event_type"] == "alert_ack"][-1]
                assert ack["details"]["identity"] == [
                    "read_velocity", "key", "k-10156"], ack["details"]
                assert ack["details"]["first_at"] and ack["details"]["at"]
                assert tc.get(f"/v1/team/alerts?org_id={TEAM}").json()[
                    "alerts"][-1]["acked"] is True

                # Now the scan slides past the bout's start: 300 newer alerts of a
                # DIFFERENT key — 300, not `_ALERT_SCAN`, so enough of the bout
                # stays visible to exercise the SLIDE (a full scan's worth would
                # push it out of the feed entirely). Same bout, moved anchor,
                # still acknowledged.
                # 300, not `_ALERT_SCAN`: enough to push the bout's first 200
                # rows out of the scan while leaving 200 of it visible. Seeding a
                # full scan's worth of newer rows would empty the window and the
                # bout would (correctly) not be in the feed at all.
                fake.seed("abuse_events", [
                    {"id": 10_000 + i, "org_id": TEAM, "event_type": "flag",
                     "rule": "disjoint_rate", "details": {"rule": "disjoint_rate"},
                     "created_at": (base + timedelta(
                         minutes=5 * (i + abuse._ALERT_SCAN + 10))).isoformat()}
                    for i in range(300)])
                slid = [a for a in tc.get(
                    f"/v1/team/alerts?org_id={TEAM}").json()["alerts"]
                    if a["type"] == "read_velocity"]
                assert slid, "the bout fell out of the feed"
                assert slid[0]["episode"] != feed[-1]["episode"], (
                    "fixture error: the key was expected to MOVE here")
                assert slid[0]["acked"] is True, (
                    "the acknowledgement evaporated on the next poll — the row "
                    "is current again and cannot be cleared")
        finally:
            env["app"].dependency_overrides.clear()

    def test_a_CLIENT_SUPPLIED_WINDOW_is_ignored(self, env):
        """The route's core invariant (round-7 review finding: it was only pinned
        at the STORE level, so an edit that forwarded `body["first_at"]` would have
        broken it silently).

        The client sends a key; the server resolves the episode against its OWN
        feed and records the window IT sees. A client-supplied window would let a
        caller acknowledge an episode it was never shown — and, worse, forge an
        overlap that covers a different bout.
        """
        self._owner(env)
        fake = env["fake"]
        self._seed_velocity(fake)
        try:
            with verdict_client(env["app"]) as tc:
                feed = tc.get(f"/v1/team/alerts?org_id={TEAM}").json()["alerts"]
                episode = feed[0]["episode"]
                r = tc.post("/v1/team/alerts/ack", json={
                    "org_id": TEAM, "episode": episode,
                    # A window from 2019, and one from the future: both must be
                    # discarded in favour of the resolved episode's own.
                    "first_at": "2019-01-01T00:00:00+00:00",
                    "at": "2099-01-01T00:00:00+00:00",
                    "identity": ["suspend"],
                })
                assert r.status_code == 200, r.text
                ack = [x for x in fake.tables["abuse_events"]
                       if x["event_type"] == "alert_ack"][-1]
                assert ack["details"]["first_at"].startswith("2026-09"), ack
                assert ack["details"]["at"].startswith("2026-09"), ack
                assert ack["details"]["identity"] == [
                    "read_velocity", "key", "k-10156"], ack
        finally:
            env["app"].dependency_overrides.clear()

    def test_a_failed_write_is_reported_not_swallowed(self, env):
        """A dismissal that does not persist must NOT return 200: the row would
        reappear on the next poll and read as a broken control."""
        self._owner(env)
        fake = env["fake"]
        self._seed_velocity(fake)
        import tortoise.supabase_control as _sc
        original = _sc.get_abuse_store

        # The stub must RESOLVE the episode, or the route 503s at the resolve
        # step and `record_alert_ack` is never reached — which is what the first
        # version of this test did while claiming to pin the write (review
        # finding). It delegates the read to the real store so only the WRITE
        # fails.
        real = _sc.get_abuse_store()

        class _FailingWrite:
            def __init__(self):
                self.writes = 0

            def recent_alerts(self, *a, **kw):
                return real.recent_alerts(*a, **kw)

            def alert_acks(self, *a, **kw):
                return real.alert_acks(*a, **kw)

            def alert_scan_saturated(self, *a, **kw):
                # Part of the store protocol the route reads. Without it the GET
                # route's best-effort `except` swallowed the AttributeError and
                # returned an EMPTY feed, which is how this test went red when the
                # probe was added — the protocol is part of the stub's contract.
                return real.alert_scan_saturated(*a, **kw)

            def record_alert_ack(self, *a, **kw):
                self.writes += 1
                # NOT an outage-shaped error: this must exercise the route's own
                # 503 branch, not `_raise_503_if_cp_outage`'s (which answers 503
                # with its own message and would make the assertion vacuous).
                raise ValueError("schema rejected the row")

        stub = _FailingWrite()
        _sc.get_abuse_store = lambda: stub
        try:
            with verdict_client(env["app"]) as tc:
                feed = tc.get(f"/v1/team/alerts?org_id={TEAM}").json()["alerts"]
                assert feed, "the fixture must expose an episode to acknowledge"
                r = tc.post("/v1/team/alerts/ack",
                            json={"org_id": TEAM, "episode": feed[0]["episode"]})
            assert stub.writes == 1, "the write was never attempted"
            assert r.status_code == 503, r.status_code
            assert "could not be stored" in r.text
            assert not [x for x in fake.tables["abuse_events"]
                        if x["event_type"] == "alert_ack"]
        finally:
            _sc.get_abuse_store = original
            env["app"].dependency_overrides.clear()

    def test_a_failed_RESOLVE_is_a_503_not_a_silent_success(self, env):
        self._owner(env)
        env["fake"].seed("abuse_events", [])
        import tortoise.supabase_control as _sc
        original = _sc.get_abuse_store

        class _Unreadable:
            def recent_alerts(self, *a, **kw):
                raise ValueError("schema rejected the read")

        _sc.get_abuse_store = lambda: _Unreadable()
        try:
            with verdict_client(env["app"]) as tc:
                r = tc.post("/v1/team/alerts/ack",
                            json={"org_id": TEAM, "episode": "x"})
            assert r.status_code == 503, r.status_code
            assert "could not be resolved" in r.text
        finally:
            _sc.get_abuse_store = original
            env["app"].dependency_overrides.clear()


# ── Operator reason read (#4874) ────────────────────────────────────────────

_INTERNAL_KEY = "internal-test-key-4874"


def _internal_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_INTERNAL_KEY}"}


class TestOperatorAbuseState:
    """#4874: ``status: flagged`` exposes no reason on any customer surface.

    The reason (rule + count + threshold + window) is recorded in
    ``abuse_events`` and is readable over the OPERATOR route only — the rule
    names are deliberately absent from ``/v1/team/alerts`` (see
    ``abuse._alert_dict``), which is why this does not ride ``/v1/team``.
    """

    def test_requires_the_internal_key(self, env, monkeypatch):
        monkeypatch.setenv("FASTAPI_INTERNAL_KEY", _INTERNAL_KEY)
        with verdict_client(env["app"]) as tc:
            bare = tc.get(f"/v1/internal/abuse/state?org_id={TEAM}")
            org_key = tc.get(f"/v1/internal/abuse/state?org_id={TEAM}",
                             headers=_auth())
            ok = tc.get(f"/v1/internal/abuse/state?org_id={TEAM}",
                        headers=_internal_headers())
        assert bare.status_code == 401
        # An org-scoped tt_ key is NOT an operator credential.
        assert org_key.status_code == 401
        assert ok.status_code == 200

    def test_unconfigured_internal_key_is_503_not_open(self, env, monkeypatch):
        monkeypatch.delenv("FASTAPI_INTERNAL_KEY", raising=False)
        with verdict_client(env["app"]) as tc:
            r = tc.get(f"/v1/internal/abuse/state?org_id={TEAM}",
                       headers={"Authorization": "Bearer anything"})
        assert r.status_code == 503

    def test_reports_the_rule_behind_the_flag(self, env, monkeypatch):
        monkeypatch.setenv("FASTAPI_INTERNAL_KEY", _INTERNAL_KEY)
        SupabaseAbuseStore(env["fake"]).flag_org(
            TEAM, "point_create",
            {"count": 6, "threshold": 5, "window_s": 3600})
        with verdict_client(env["app"]) as tc:
            r = tc.get(f"/v1/internal/abuse/state?org_id={TEAM}",
                       headers=_internal_headers())
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "flagged"
        assert body["flagged_at"] is not None
        assert body["flags_error"] is None
        [ep] = body["flags"]
        assert ep["rule"] == "point_create"
        assert (ep["count"], ep["threshold"], ep["window_s"]) == (6, 5, 3600)
        assert ep["since"] is not None

    def test_active_org_reports_no_episode(self, env, monkeypatch):
        monkeypatch.setenv("FASTAPI_INTERNAL_KEY", _INTERNAL_KEY)
        with verdict_client(env["app"]) as tc:
            body = tc.get(f"/v1/internal/abuse/state?org_id={TEAM}",
                          headers=_internal_headers()).json()
        assert body["status"] == "active"
        assert body["flags"] == []  # readable, and genuinely none

    def test_unreadable_store_is_not_reported_as_no_flags(self, env,
                                                         monkeypatch):
        """The two states an operator must never confuse: "no active
        episode" and "could not read". A failure reports ``flags: null``
        plus ``flags_error`` — never an empty list."""
        monkeypatch.setenv("FASTAPI_INTERNAL_KEY", _INTERNAL_KEY)

        class _Broken:
            def org_state(self, org_id):
                return {"found": True, "flagged_at": None,
                        "suspended": False}

            def active_flag_episodes(self, org_id):
                raise RuntimeError("abuse_events unreachable")

        monkeypatch.setattr(sc, "get_abuse_store", lambda: _Broken())
        with verdict_client(env["app"]) as tc:
            body = tc.get(f"/v1/internal/abuse/state?org_id={TEAM}",
                          headers=_internal_headers()).json()
        assert body["flags"] is None
        assert "abuse_events unreachable" in body["flags_error"]

    def test_unreadable_org_state_is_null_never_active(self, env, monkeypatch):
        """The org-state half has its own sentinel: when that read fails the
        status is null, NOT a derived "active" from a half-read org — the
        operator must see "could not read", never "clean"."""
        monkeypatch.setenv("FASTAPI_INTERNAL_KEY", _INTERNAL_KEY)

        class _HalfBroken:
            def org_state(self, org_id):
                raise RuntimeError("organizations unreachable")

            def active_flag_episodes(self, org_id):
                return []

        monkeypatch.setattr(sc, "get_abuse_store", lambda: _HalfBroken())
        with verdict_client(env["app"]) as tc:
            body = tc.get(f"/v1/internal/abuse/state?org_id={TEAM}",
                          headers=_internal_headers()).json()
        assert body["status"] is None
        assert body["suspended"] is None
        assert "organizations unreachable" in body["org_state_error"]

    def test_unknown_org_is_not_a_clean_org(self, env, monkeypatch):
        """``org_id`` is typed by hand on this route, so a typo is a real
        case. It must not come back as ``status: "active"`` + ``flags: []`` +
        no error — that is a confident clean bill of health for an org that
        does not exist."""
        monkeypatch.setenv("FASTAPI_INTERNAL_KEY", _INTERNAL_KEY)
        with verdict_client(env["app"]) as tc:
            body = tc.get("/v1/internal/abuse/state?org_id=no-such-org",
                          headers=_internal_headers()).json()
        assert body["org_found"] is False
        assert body["status"] == "unknown_org"
        assert body["flags"] == []

    def test_registry_mode_status_comes_from_the_durable_prop(self, env,
                                                              monkeypatch):
        """#4874 review P2, at the route: in registry/selfhost mode the
        store's dicts are per-process, so after a restart a flagged org would
        read ``active`` while ``/v1/team`` renders ``flagged`` from the durable
        prop. The route must answer from the prop (the same source) — and
        report it as found."""
        monkeypatch.setenv("FASTAPI_INTERNAL_KEY", _INTERNAL_KEY)
        nodes = {TEAM: {"flagged_at": "2026-09-23T08:30:35+00:00",
                        "suspended_at": "2026-09-23T09:00:00+00:00"}}

        def _read(org_id):
            node = nodes.get(org_id)
            if node is None:
                return {"found": False, "flagged_at": None,
                        "suspended_at": None}
            return {"found": True, **node}

        # A fresh store = a restarted selfhost process: no rows, no dicts.
        monkeypatch.setattr(sc, "get_abuse_store",
                            lambda: abuse.MemoryAbuseStore(registry_read=_read))
        with verdict_client(env["app"]) as tc:
            body = tc.get(f"/v1/internal/abuse/state?org_id={TEAM}",
                          headers=_internal_headers()).json()
        assert body["org_found"] is True
        assert body["status"] == "flagged"
        assert body["flagged_at"] == "2026-09-23T08:30:35+00:00"
        assert body["suspended"] is True
        # The episode rows ARE volatile: empty, not invented.
        assert body["flags"] == []

    def test_no_store_at_all_is_null_everywhere_not_active(self, env,
                                                          monkeypatch):
        """A store that cannot even be BUILT (broken control-plane config)
        must not read as a clean org: both answers are null, not "active" /
        "no flags"."""
        monkeypatch.setenv("FASTAPI_INTERNAL_KEY", _INTERNAL_KEY)

        def _boom():
            raise RuntimeError("no control plane")

        monkeypatch.setattr(sc, "get_abuse_store", _boom)
        with verdict_client(env["app"]) as tc:
            body = tc.get(f"/v1/internal/abuse/state?org_id={TEAM}",
                          headers=_internal_headers()).json()
        assert body["status"] is None
        assert body["flags"] is None
        assert "no control plane" in body["org_state_error"]
        assert "no control plane" in body["flags_error"]

    def test_cleared_episode_stops_being_reported(self, env, monkeypatch):
        monkeypatch.setenv("FASTAPI_INTERNAL_KEY", _INTERNAL_KEY)
        store = SupabaseAbuseStore(env["fake"])
        store.flag_org(TEAM, "point_create", {"count": 6, "threshold": 5})
        store.flag_clear(TEAM, "point_create")
        with verdict_client(env["app"]) as tc:
            body = tc.get(f"/v1/internal/abuse/state?org_id={TEAM}",
                          headers=_internal_headers()).json()
        assert body["flags"] == []
        assert body["flagged_at"] is None
        assert body["status"] == "active"

    def test_durable_read_agrees_with_latest_flag_at(self, env):
        """The operator read folds the SAME rows the enforcement path reads;
        on the durable store too, both must call the episode active."""
        store = SupabaseAbuseStore(env["fake"])
        store.flag_org(TEAM, "point_create", {"count": 6, "threshold": 5})
        reported = {e["rule"] for e in store.active_flag_episodes(TEAM)}
        assert reported == {"point_create"}
        assert store.latest_flag_at(TEAM, "point_create") is not None


# ── Turnstile on the signup endpoint ────────────────────────────────────────

class TestTurnstileSignup:
    def test_captcha_400_when_challenge_fails(self, monkeypatch, env):
        monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret")
        monkeypatch.setattr("httpx.post",
                            lambda *a, **k: _Resp({"success": False}))
        with verdict_client(env["app"]) as tc:
            r = tc.post("/v1/signup/email", json={
                "email": "cap@x.co", "password": "hunter2secret",
                "cf-turnstile-response": "bad-token"})
        assert r.status_code == 400
        assert "security check" in r.json()["detail"]

    def test_captcha_passes_and_flow_continues(self, monkeypatch, env):
        monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret")
        monkeypatch.setattr("httpx.post",
                            lambda *a, **k: _Resp({"success": True}))
        with verdict_client(env["app"]) as tc:
            r = tc.post("/v1/signup/email", json={
                "email": "cap@x.co", "password": "hunter2secret",
                "cf-turnstile-response": "good-token"})
        # NOT the CAPTCHA 400 — the flow continued past siteverify (503 here:
        # SUPABASE_URL unset in this test env, the endpoint's own guard).
        assert r.status_code != 400

    def test_fail_open_without_secret(self, monkeypatch, env):
        monkeypatch.delenv("TURNSTILE_SECRET_KEY", raising=False)
        with verdict_client(env["app"]) as tc:
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
        """#4113: the non-SDK (empty-binding) writers are a DECLARED set that is
        write-classified — not ad-hoc literal assertions that a rename erases."""
        from tool_surface_capabilities import (  # noqa: I001
            NON_SDK_WRITER_TOOLS, quota_gated_wrap_sites, registry_entries_by_method,
        )
        from tortoise.mcp_server import WRITE_TOOL_NAMES
        # every _quota_gated wrap site's (HTTP) tool is write-classified
        by_method = registry_entries_by_method()
        wrapped = {m for m, _, _ in quota_gated_wrap_sites().sites}
        assert wrapped, "wrap-site scan is vacuous"
        for method in sorted(wrapped):
            for entry in by_method.get(method, []):
                if entry.http_policy:
                    assert entry.name in WRITE_TOOL_NAMES, (entry.name, method)
        # non-SDK (empty-binding) writers are a declared, write-classified set.
        # Derived from the SERVED set (#3883): a retired empty-binding writer is
        # still callable by name, so it belongs in the declared set too.
        from tool_surface_capabilities import served_registry as _served
        assert NON_SDK_WRITER_TOOLS <= WRITE_TOOL_NAMES
        empty_writers = {e.name for e in _served()
                         if not e.sdk_method and e.name in WRITE_TOOL_NAMES}
        assert empty_writers == set(NON_SDK_WRITER_TOOLS)

    def test_point_creating_wrap_sites_have_weights(self):
        """#4113: per-SITE AST extraction (the old ``str.find`` went -1 the
        moment a handler merged behind a dispatch table).  The weight family is
        exactly WEIGHT_BEARING_METHODS; every other wrap site must not weigh."""
        from tool_surface_capabilities import (  # noqa: I001
            WEIGHT_BEARING_METHODS, quota_gated_wrap_sites, wrap_site_violations,
        )
        sites = quota_gated_wrap_sites()
        assert sites.unresolved == (), sites.unresolved
        assert sites.sites, "wrap-site scan is vacuous"
        assert wrap_site_violations() == []
        weighted = {m for m, _, w in sites.sites if w}
        assert weighted == set(WEIGHT_BEARING_METHODS), weighted

    def test_no_write_tool_counted_as_read(self):
        """#4113: derive the ACTUAL wrap sites from the AST (not a text find),
        then assert the declared write-surface map is live in BOTH directions:
        every wrapped method is mapped, AND every mapped method still has a wrap
        site (the missing inverse — a declared write whose wrap was removed must
        fail, not silently stop being checked).  Every mapped name must resolve
        to a live registry entry so a rename fails loudly."""
        from tool_surface_capabilities import write_surface_map_violations
        violations = write_surface_map_violations()
        assert violations == [], "; ".join(violations)

    def test_destructive_mutating_tools_never_read_classified(self):
        """#4113: capability-derived — every handler-reached graph mutation must
        be write-classified (or a self-guarding HTTP-excluded tool).  The old
        hardcoded destructive set could not fire for a merged/renamed tool, and
        the annotation-derived set went empty when a merge picked read-only."""
        from tool_surface_capabilities import (
            served_registry,
            write_classification_violations,
        )
        violations = write_classification_violations(served_registry())
        assert violations == [], (
            "a graphs:read-only MCP key could reach a write: " + "; ".join(violations))

    def test_rw_annotated_http_tools_never_read_classified(self):
        """#4113: the ground truth is the wrap sites / the SDK source — NOT the
        registry annotation.  Every wrap-site operation's bound tool must be
        write-classified (or a self-guarding HTTP-excluded tool), so a merge
        that picks the read-only label cannot blind the guard."""
        from tool_surface_capabilities import (  # noqa: I001
            handler_self_guards, quota_gated_wrap_sites, registry_entries_by_method,
        )
        import tortoise.mcp_server as ms

        by_method = registry_entries_by_method()
        wrapped = {m for m, _, _ in quota_gated_wrap_sites().sites}
        assert wrapped, "wrap-site scan is vacuous"
        for method in sorted(wrapped):
            for entry in by_method.get(method, []):
                assert entry.name in ms.WRITE_TOOL_NAMES or (
                    entry.http_policy is False and handler_self_guards(entry.name)
                ), (f"{entry.name} wraps write {method!r} but is neither "
                    f"write-classified nor a self-guarding HTTP-excluded tool")

    def test_mcp_read_hook_classification(self, monkeypatch):
        """maybe_record_mcp_read: writes skipped, reads counted, selfhost and
        no-team skipped, kill-switch respected."""
        import tortoise.mcp_server as ms  # noqa: I001
        import tortoise.abuse as abuse_mod
        calls = []
        monkeypatch.setattr(abuse_mod, "record_read",
                            lambda key_id, org_id, now=None:
                            calls.append((key_id, org_id)))
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
        i_ingest = src.find("_get_org_sdk().ingest,")
        assert i_ingest != -1 and '.get("points")' in src[i_ingest:i_ingest + 400]
        i_ckpt = src.find("_get_org_sdk().checkpoint,")
        assert i_ckpt != -1 and '.get("filed")' in src[i_ckpt:i_ckpt + 400]
        # capture_session weight lives in hosted_api (REST seam)
        import tortoise.hosted_api as ha
        ha_src = Path(ha.__file__).read_text()
        i_cap = ha_src.find("len(body.conversation) + len(extracted)")
        assert i_cap != -1  # capture_session weights actual created Points


def test_the_client_seam_turns_a_wait_bound_refusal_into_an_inconclusive_skip(
        monkeypatch):
    """#8818: the deterministic refusal pin for this module's client seams.

    No load and no wall-clock wait: the bound is forced to 0 s, so the refusal
    comes from the REAL ``WaitBoundMiddleware``. Every construction of the hosted
    app is converted — every ``TestClient(env["app"])`` (the ``env`` fixture's
    hosted app, shared by all REST test classes) — so the seam covers every REST
    read bound from them: ``GET /v1/team/keys`` (the busiest), ``GET /v1/team``
    (the route this pin drives), ``GET /v1/team/alerts?org_id=``, ``DELETE
    /v1/team/keys/{kid}``, ``POST /v1/points``, ``POST /v1/session/key``,
    ``POST /v1/signup/email`` and the session-JWT REST lane — none of them exempt
    from the bound.

    What is NOT converted, and why: ``TestMcp._mcp_client`` wraps a local
    ``Starlette`` carrying ONLY ``OrgResolutionMiddleware``, so no
    ``WaitBoundMiddleware`` is on its path and none of its reads can be a
    refusal — its ``status_code == 200`` reads are answered by that local echo
    route, never by the hosted app. This pin's own ``TestClient(ha.app)`` is
    plain DELIBERATELY and briefly: it is the non-vacuity control described at
    the end of this docstring.

    Neither this paragraph nor the enumeration above records a COUNT of
    constructions. It said "twenty-five of twenty-six" until this PR added tests
    and made it wrong — a numeral here re-stales on every test added, so the
    property is what is stated: no construction of the hosted app is exempt, and
    each exception says why it cannot reach the bound.

    The plain-client control is the non-vacuity leg: it is the exact
    ``assert 504 == 200`` shape this conversion removes, and it goes RED if the
    route is ever exempted (the middleware stops refusing, so the control's 504
    assertion fails).
    """
    import tortoise.hosted_api as ha
    from tortoise import mcp_auth
    from tortoise.hosted_api import _TRANSPORT_WAIT_BOUND_EXEMPT

    route = ("GET", "/v1/team")
    assert route not in _TRANSPORT_WAIT_BOUND_EXEMPT
    monkeypatch.setattr(mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 0.0)
    with TestClient(ha.app) as raw:
        assert raw.get(route[1]).status_code == 504
    with verdict_client(ha.app) as tc, pytest.raises(pytest.skip.Exception) as excinfo:
        tc.get(route[1])
    message = str(excinfo.value)
    assert "INCONCLUSIVE [#5049]" in message
    assert "GET /v1/team answered" in message
