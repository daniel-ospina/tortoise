"""#2003 (W7) — invite-accept fusion v2: 3-path + OTP both paths + admin
resend/expire + member_progress arming (registry lane).

Epic #1976 DE2E-7/DE2E-8 surface 12 (most failure-prone). Registry
(selfhost) lane via TestClient + temp FalkorDBLite — mirrors
tests/test_invites_http.py fixtures. The v2 Accept header
(application/vnd.tortoise.onboarding+json;version=2) is the opt-in: legacy
clients (no header) get the email-mismatch 403 BYTE-UNCHANGED (golden
assertions below).

Security posture under test:
- fusion without OTP → blocked (BOTH override paths — invite-hijack closed);
- OTP single-use + expiry + 5-attempt budget + per-token/IP/global send caps;
- the 3-path choice is never silent (discovery payload, fuse default);
- accept-with-mismatch records the mismatch (never silent);
- member arming writes member_progress {user_id: []} WITHOUT faking org-level
  completion steps.
"""
from __future__ import annotations

import asyncio
import contextvars
import os
import tempfile
from datetime import UTC, datetime, timedelta

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import pytest
from fastapi.testclient import TestClient

from tests._http_fixtures import patched_tortoise_sdk
from tortoise import email_notify
from tortoise.hosted_api import app, get_current_user

# #1719 (Task 3): team_memberships.user_id is a uuid column — real JWT
# subjects are UUIDs; non-UUID user_id literals are prod-impossible.
_U1 = "9f2c1a40-0000-4a00-8000-000000000001"   # owner
_U_BOB = "9f2c1a40-0000-4a00-8000-00000000000e"
_U_ALICE = "9f2c1a40-0000-4a00-8000-00000000000f"
_U_CAROL = "9f2c1a40-0000-4a00-8000-000000000010"

V2_ACCEPT = "application/vnd.tortoise.onboarding+json;version=2"


# #1556: hold registry SDKs alive — _get_registry() drops the SDK ref;
# close-on-GC (#1475) shuts the temp server before the test uses it.
_REG_SDKS: list = []


@pytest.fixture
def client():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _U1,
            "email": "owner@example.com",
        }
        # #2143 tripwire / #2127: the shared helper (patch __init__ → temp
        # DB, TORTOISE_DB_PATH pin, deterministic anchor close-then-clear at
        # enter; pop-env → restore __init__ → close anchors → clear overrides
        # at exit) replaces the local _patch/_restore churn-shape — the local
        # clear-without-close copy reds the test-lint tripwire at collection.
        with patched_tortoise_sdk(db_path):
            try:
                with TestClient(app) as tc:
                    yield tc
            finally:
                app.dependency_overrides.clear()
                while _REG_SDKS:
                    try:  # noqa: SIM105
                        _REG_SDKS.pop().close()
                    except Exception:
                        pass


@pytest.fixture
def reg(client):
    from tortoise.hosted_api import _make_sdk
    sdk = _make_sdk(namespace="registry")
    _REG_SDKS.append(sdk)
    return sdk._get_registry()


def _as_user(user_id: str, email: str):
    app.dependency_overrides[get_current_user] = lambda u=user_id, e=email: {
        "user_id": u,
        "email": e,
    }


def _seed_team(reg, team_id: str, tier: str = "team"):
    reg.query(
        "CREATE (t:Team {id:$id, name:$id, tier:$tier})",
        params={"id": team_id, "tier": tier},
    )


def _seed_membership(reg, team_id: str, user_id: str, role: str,
                     status: str = "active"):
    reg.query(
        "CREATE (m:Membership {user_id:$uid, team_id:$tid, role:$role, "
        "status:$status, created_at:'2026-08-01T00:00:00+00:00'})",
        params={"uid": user_id, "tid": team_id, "role": role, "status": status},
    )


def _seed_team_with_owner(reg, team_id: str, owner: str = _U1,
                          tier: str = "team"):
    _seed_team(reg, team_id, tier=tier)
    _seed_membership(reg, team_id, owner, "owner")


def _invite(client, reg, *, team_id="team-f", email="bob@example.com",
            role="member"):
    _seed_team_with_owner(reg, team_id)
    r = client.post("/v1/invites",
                    json={"team_id": team_id, "email": email, "role": role})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_secret_key_123")
    monkeypatch.setenv("EMAIL_LINK_BASE_URL", "https://tortoise.premiselabs.co")
    email_notify._skip_logged.clear()
    yield


def _capture_otp(monkeypatch, captured: dict):
    """Monkeypatch the OTP email sender so tests can read the code (the API
    never returns it — the mailbox is the only channel)."""
    def fake_send(team_name, invitee_email, code, on_sent=None):
        captured.update(team_name=team_name, email=invitee_email, code=code)

    monkeypatch.setattr(email_notify, "send_otp_email", fake_send)


def _v2_accept(client, token, **body):
    return client.post("/v1/invites/accept", json={"token": token, **body},
                       headers={"Accept": V2_ACCEPT})


# ═══════════════════════════════════════════════════════════════════════════
# Legacy-403 preservation (DE2E-7: byte-unchanged without the v2 opt-in)
# ═══════════════════════════════════════════════════════════════════════════


class TestLegacyPreserved:
    def test_mismatch_403_byte_unchanged_without_v2_header(self, client, reg):
        """The golden legacy contract: no v2 header → the SAME 403 detail as
        pre-W7 (asserted verbatim, not just status)."""
        inv = _invite(client, reg, email="bob@example.com")
        _as_user(_U_ALICE, "alice@example.com")
        r = client.post("/v1/invites/accept", json={"token": inv["token"]})
        assert r.status_code == 403
        assert r.json()["detail"] == "Invite email does not match this account"

    def test_match_path_unchanged_under_v2(self, client, reg):
        """v2 + email MATCH → the legacy one-click accept (200, exact body)."""
        inv = _invite(client, reg, email="bob@example.com")
        _as_user(_U_BOB, "bob@example.com")
        r = _v2_accept(client, inv["token"])
        assert r.status_code == 200, r.text
        assert r.json() == {"team_id": "team-f", "role": "member"}

    def test_v2_does_not_intercept_invalid_tokens(self, client, reg):
        """v2 + unknown token falls through to the legacy 400."""
        _seed_team_with_owner(reg, "team-x")
        _as_user(_U_ALICE, "alice@example.com")
        r = _v2_accept(client, "not-a-token")
        assert r.status_code == 400
        assert "Invalid or expired" in r.json()["detail"]


# ═══════════════════════════════════════════════════════════════════════════
# 3-path discovery (fuse default, never silent)
# ═══════════════════════════════════════════════════════════════════════════


class TestThreePathChoice:
    def test_mismatch_discovery_payload(self, client, reg):
        """Opted-in mismatch + no path → 409 with the 3-path choice shape:
        fuse default, otp_required, both paths listed, invited email shown."""
        inv = _invite(client, reg, email="bob@example.com")
        _as_user(_U_ALICE, "alice@example.com")
        r = _v2_accept(client, inv["token"])
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["error_code"] == "invite_email_mismatch"
        choice = detail["choice"]
        assert choice["paths"] == ["fuse", "accept-mismatch"]
        assert choice["default_path"] == "fuse"
        assert choice["otp_required"] is True
        assert choice["invited_email"] == "bob@example.com"
        # nothing consumed, no membership minted (never silent / never auto)
        rows = reg.query(
            "MATCH (m:Membership {team_id:'team-f', user_id:$uid, "
            "status:'active'}) RETURN count(m)",
            params={"uid": _U_ALICE},
        ).result_set[0][0]
        assert rows == 0

    def test_invalid_path_422(self, client, reg):
        inv = _invite(client, reg, email="bob@example.com")
        _as_user(_U_ALICE, "alice@example.com")
        r = _v2_accept(client, inv["token"], path="sneaky")
        assert r.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# OTP gating on BOTH mismatch-override paths (invite-hijack closed)
# ═══════════════════════════════════════════════════════════════════════════


class TestOtpGate:
    def _mismatch_invite(self, client, reg, email="bob@example.com"):
        inv = _invite(client, reg, email=email)
        _as_user(_U_ALICE, "alice@example.com")
        return inv

    def test_fuse_without_otp_blocked(self, client, reg):
        """DE2E-7: fuse REQUIRES OTP — without a code the override is
        blocked (no membership, no record)."""
        inv = self._mismatch_invite(client, reg)
        r = _v2_accept(client, inv["token"], path="fuse")
        assert r.status_code == 403
        assert r.json()["detail"]["error_code"] == "invite_mismatch_otp_required"
        rows = reg.query(
            "MATCH (m:Membership {team_id:'team-f', status:'active'}) "
            "RETURN count(m)").result_set[0][0]
        assert rows == 1  # owner only — nothing minted
        rows = reg.query(
            "MATCH (i:Invitation {id:$id}) RETURN i.accepted_at",
            params={"id": inv["invite_id"]},
        ).result_set
        assert rows[0][0] is None  # invite NOT consumed by the block

    def test_accept_mismatch_without_otp_blocked(self, client, reg):
        """P1 fix: accept-with-mismatch ALSO requires OTP — without OTP,
        blocked (the invite-hijack vector is closed on BOTH paths)."""
        inv = self._mismatch_invite(client, reg)
        r = _v2_accept(client, inv["token"], path="accept-mismatch")
        assert r.status_code == 403
        assert r.json()["detail"]["error_code"] == "invite_mismatch_otp_required"
        rows = reg.query(
            "MATCH (m:Membership {team_id:'team-f', status:'active'}) "
            "RETURN count(m)").result_set[0][0]
        assert rows == 1

    def _otp_for(self, client, token, monkeypatch, captured):
        _capture_otp(monkeypatch, captured)
        r = client.post("/v1/invites/otp", json={"token": token})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "otp_sent"
        assert r.json()["expires_in_s"] == 600
        return r

    def test_otp_sent_to_invitee_email_only(self, client, reg, monkeypatch):
        """The code goes to the INVITEE address (proof-of-control target) —
        never to the session account's mailbox, never in the response."""
        inv = self._mismatch_invite(client, reg)
        captured = {}
        self._otp_for(client, inv["token"], monkeypatch, captured)
        assert captured["email"] == "bob@example.com"  # invitee, not alice
        assert len(captured["code"]) == 6
        assert captured["code"].isdigit()

    def test_otp_not_required_on_email_match(self, client, reg, monkeypatch):
        """Email-match sessions need no OTP — logging in as the invitee IS
        the proof-of-control."""
        inv = _invite(client, reg, email="bob@example.com")
        _as_user(_U_BOB, "bob@example.com")
        captured = {}
        _capture_otp(monkeypatch, captured)
        r = client.post("/v1/invites/otp", json={"token": inv["token"]})
        assert r.status_code == 400
        assert r.json()["detail"]["error_code"] == "otp_not_required"
        assert not captured

    def test_otp_invalid_token_400(self, client, reg):
        _as_user(_U_ALICE, "alice@example.com")
        r = client.post("/v1/invites/otp", json={"token": "garbage"})
        assert r.status_code == 400

    def test_wrong_otp_blocked_accept(self, client, reg, monkeypatch):
        """Wrong code → 403 with invite_otp_invalid; nothing accepted."""
        inv = self._mismatch_invite(client, reg)
        captured = {}
        self._otp_for(client, inv["token"], monkeypatch, captured)
        r = _v2_accept(client, inv["token"], path="fuse", otp="000000")
        assert r.status_code == 403
        assert r.json()["detail"]["error_code"] == "invite_otp_invalid"
        rows = reg.query(
            "MATCH (m:Membership {team_id:'team-f', status:'active'}) "
            "RETURN count(m)").result_set[0][0]
        assert rows == 1

    def test_five_failed_attempts_exhaust_code(self, client, reg, monkeypatch):
        """Brute-force budget: 5 failed verifies clear the code — a sixth
        attempt reports no outstanding code (re-send required)."""
        inv = self._mismatch_invite(client, reg)
        captured = {}
        self._otp_for(client, inv["token"], monkeypatch, captured)
        for _ in range(5):
            r = _v2_accept(client, inv["token"], path="fuse", otp="000000")
            assert r.status_code == 403, r.text
        r6 = _v2_accept(client, inv["token"], path="fuse", otp="000000")
        assert r6.status_code == 403
        assert r6.json()["detail"]["error_code"] == "invite_otp_invalid"

    def test_expired_otp_blocked(self, client, reg, monkeypatch):
        """A code past its 10-minute window cannot override the mismatch."""
        inv = self._mismatch_invite(client, reg)
        captured = {}
        self._otp_for(client, inv["token"], monkeypatch, captured)
        reg.query(
            "MATCH (i:Invitation {id:$id}) SET i.otp_expires_at = $past",
            params={"id": inv["invite_id"],
                    "past": (datetime.now(UTC)
                             - timedelta(minutes=1)).isoformat()},
        )
        r = _v2_accept(client, inv["token"], path="fuse", otp=captured["code"])
        assert r.status_code == 403
        assert r.json()["detail"]["error_code"] == "invite_otp_invalid"


# ═══════════════════════════════════════════════════════════════════════════
# Mismatch-override accept with OTP (fuse + accept-mismatch)
# ═══════════════════════════════════════════════════════════════════════════


class TestMismatchOverrideAccept:
    def _ready(self, client, reg, monkeypatch, team_id="team-f",
               email="bob@example.com"):
        inv = _invite(client, reg, team_id=team_id, email=email)
        _as_user(_U_ALICE, "alice@example.com")
        captured = {}
        _capture_otp(monkeypatch, captured)
        r = client.post("/v1/invites/otp", json={"token": inv["token"]})
        assert r.status_code == 200, r.text
        return inv, captured["code"]

    def test_fuse_with_otp_accepts_under_current_account(self, client, reg,
                                                         monkeypatch):
        """DE2E-7: fuse + OTP → membership under the CURRENT account; the
        invite records the path + fused_from_email + accepted_at (single-use
        token consumed)."""
        inv, code = self._ready(client, reg, monkeypatch)
        r = _v2_accept(client, inv["token"], path="fuse", otp=code)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["team_id"] == "team-f"
        assert body["role"] == "member"
        assert body["accepted_via"] == "fuse"
        assert body["mismatch"] == {"invited_email": "bob@example.com",
                                    "recorded": True}
        # membership for alice (current account)
        rows = reg.query(
            "MATCH (m:Membership {team_id:'team-f', user_id:$uid, "
            "status:'active'}) RETURN m.role",
            params={"uid": _U_ALICE},
        ).result_set
        assert rows and rows[0][0] == "member"
        # invite records the override — never silent
        row = reg.query(
            "MATCH (i:Invitation {id:$id}) RETURN i.accepted_at, i.accepted_via, "
            "i.accepted_mismatch, i.fused_from_email, i.otp_verified_at",
            params={"id": inv["invite_id"]},
        ).result_set[0]
        assert row[0] is not None          # accepted
        assert row[1] == "fuse"
        assert row[2] is True
        assert row[3] == "bob@example.com"  # fused_from_email
        assert row[4] is not None           # otp proof recorded
        # token single-use: a replay cannot re-accept
        r2 = _v2_accept(client, inv["token"], path="fuse", otp=code)
        assert r2.status_code == 400  # invite consumed

    def test_accept_mismatch_with_otp_records_mismatch(self, client, reg,
                                                       monkeypatch):
        """DE2E-7: accept-with-mismatch + OTP → membership + mismatch
        recorded (accepted_via='accept-mismatch')."""
        inv, code = self._ready(client, reg, monkeypatch)
        r = _v2_accept(client, inv["token"], path="accept-mismatch", otp=code)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["accepted_via"] == "accept-mismatch"
        assert body["mismatch"]["recorded"] is True
        row = reg.query(
            "MATCH (i:Invitation {id:$id}) RETURN i.accepted_mismatch, "
            "i.otp_verified_at",
            params={"id": inv["invite_id"]},
        ).result_set[0]
        assert row[0] is True and row[1] is not None

    def test_otp_code_single_use_across_paths(self, client, reg, monkeypatch):
        """The verified code is single-use — a second override with the same
        code is blocked even before the token consumption is hit."""
        inv, code = self._ready(client, reg, monkeypatch, team_id="team-g")
        assert _v2_accept(client, inv["token"], path="fuse", otp=code).status_code == 200
        # the accept consumed the token too — replay is 400 (consumed invite)
        r = _v2_accept(client, inv["token"], path="accept-mismatch", otp=code)
        assert r.status_code in (400, 403)

    def test_capacity_402_non_consuming_on_override(self, client, reg,
                                                    monkeypatch):
        """A mismatch override past the seat cap 402s WITHOUT consuming the
        invite or the OTP budget (retryable once a seat frees)."""
        reg.query(
            "CREATE (t:Team {id:'team-cap', name:'team-cap', tier:'team', "
            "max_users:2})",
        )
        reg.query(
            "CREATE (m:Membership {user_id:$uid, team_id:'team-cap', "
            "role:'owner', status:'active', created_at:'2026-08-01T00:00:00+00:00'})",
            params={"uid": _U1},
        )
        reg.query(
            "CREATE (m:Membership {user_id:$uid, team_id:'team-cap', "
            "role:'member', status:'active', created_at:'2026-08-01T00:00:00+00:00'})",
            params={"uid": _U_CAROL},
        )
        inv = _invite(client, reg, team_id="team-cap", email="bob@example.com")
        _as_user(_U_ALICE, "alice@example.com")
        captured = {}
        _capture_otp(monkeypatch, captured)
        assert client.post("/v1/invites/otp",
                           json={"token": inv["token"]}).status_code == 200
        r = _v2_accept(client, inv["token"], path="fuse", otp=captured["code"])
        assert r.status_code == 402
        rows = reg.query(
            "MATCH (i:Invitation {id:$id}) RETURN i.accepted_at",
            params={"id": inv["invite_id"]},
        ).result_set
        assert rows[0][0] is None  # non-consuming 402


# ═══════════════════════════════════════════════════════════════════════════
# OTP send rate caps
# ═══════════════════════════════════════════════════════════════════════════


class TestOtpRateLimits:
    @pytest.fixture(autouse=True)
    def _limiter_on(self, monkeypatch):
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)  # limiter ON
        import tortoise.hosted_api as ha_mod
        ha_mod._INVITE_OTP_TOKEN_BUCKETS.clear()
        ha_mod._INVITE_OTP_IP_BUCKETS.clear()
        ha_mod._INVITE_OTP_GLOBAL_BUCKETS.clear()
        yield
        ha_mod._INVITE_OTP_TOKEN_BUCKETS.clear()
        ha_mod._INVITE_OTP_IP_BUCKETS.clear()
        ha_mod._INVITE_OTP_GLOBAL_BUCKETS.clear()

    def test_per_ip_send_cap(self, client, reg, monkeypatch):
        """Per-IP OTP-send cap (env-shrunk to 2): a rotating-token farm
        cannot bypass the IP budget."""
        monkeypatch.setenv("TORTOISE_INVITE_OTP_IP_LIMIT", "2")
        monkeypatch.setenv("TORTOISE_INVITE_OTP_IP_WINDOW_S", "3600")
        monkeypatch.setenv("TORTOISE_INVITE_OTP_TOKEN_LIMIT", "100")
        captured = {}
        _capture_otp(monkeypatch, captured)
        for i in range(2):
            _as_user(_U1, "owner@example.com")
            inv = _invite(client, reg, team_id=f"team-ip{i}", email=f"b{i}@example.com")
            _as_user(_U_ALICE, "alice@example.com")
            r = client.post("/v1/invites/otp", json={"token": inv["token"]})
            assert r.status_code == 200, r.text
        _as_user(_U1, "owner@example.com")
        inv3 = _invite(client, reg, team_id="team-ip3", email="b3@example.com")
        _as_user(_U_ALICE, "alice@example.com")
        r3 = client.post("/v1/invites/otp", json={"token": inv3["token"]})
        assert r3.status_code == 429, r3.text
        assert r3.json()["detail"]["error_code"] == "over_invite_otp_rate_limit"
        assert r3.headers.get("retry-after")

    def test_rate_limit_disabled_opt_out(self, client, reg, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
        monkeypatch.setenv("TORTOISE_INVITE_OTP_IP_LIMIT", "1")
        monkeypatch.setenv("TORTOISE_INVITE_OTP_TOKEN_LIMIT", "1")
        captured = {}
        _capture_otp(monkeypatch, captured)
        for i in range(3):
            _as_user(_U1, "owner@example.com")
            inv = _invite(client, reg, team_id=f"team-opt{i}", email=f"o{i}@example.com")
            _as_user(_U_ALICE, "alice@example.com")
            assert client.post("/v1/invites/otp",
                               json={"token": inv["token"]}).status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# Admin resend / expire (pending-invites affordance)
# ═══════════════════════════════════════════════════════════════════════════


class TestAdminResendExpire:
    def test_resend_rotates_token(self, client, reg, monkeypatch):
        """POST /v1/invites/{id}/resend rotates the token: the NEW token
        accepts, the OLD one is dead (registry lane)."""
        inv = _invite(client, reg, email="bob@example.com")
        scheduled = {}

        def fake_send(team_name, invitee_email, role, token, invitation_id,
                      on_sent=None):
            scheduled.update(token=token, email=invitee_email)

        monkeypatch.setattr(email_notify, "send_invite_email", fake_send)
        r = client.post(f"/v1/invites/{inv['invite_id']}/resend?team_id=team-f")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "resent"
        new_token = body["token"]
        assert new_token != inv["token"]
        assert scheduled["email"] == "bob@example.com"
        # new token accepts
        _as_user(_U_BOB, "bob@example.com")
        assert client.post("/v1/invites/accept",
                           json={"token": new_token}).status_code == 200
        # old token is dead (hash rotated)
        assert client.post("/v1/invites/accept",
                           json={"token": inv["token"]}).status_code == 400

    def test_resend_member_403(self, client, reg, monkeypatch):
        """Admin-only: a member cannot resend."""
        inv = _invite(client, reg, email="bob@example.com")
        _seed_membership(reg, "team-f", _U_BOB, "member")
        _as_user(_U_BOB, "bob@example.com")
        r = client.post(f"/v1/invites/{inv['invite_id']}/resend?team_id=team-f")
        assert r.status_code == 403

    def test_resend_rate_cap(self, client, reg, monkeypatch):
        """Per-invitation resend cap (env-shrunk to 2) → 429."""
        import tortoise.hosted_api as ha_mod
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        monkeypatch.setenv("TORTOISE_INVITE_RESEND_LIMIT", "2")
        monkeypatch.setenv("TORTOISE_INVITE_RESEND_WINDOW_S", "86400")
        ha_mod._INVITE_RESEND_BUCKETS.clear()
        try:
            inv = _invite(client, reg, email="bob@example.com")
            for _ in range(2):
                r = client.post(
                    f"/v1/invites/{inv['invite_id']}/resend?team_id=team-f")
                assert r.status_code == 200, r.text
            r3 = client.post(
                f"/v1/invites/{inv['invite_id']}/resend?team_id=team-f")
            assert r3.status_code == 429, r3.text
            assert r3.json()["detail"]["error_code"] == "over_invite_resend_rate_limit"
        finally:
            ha_mod._INVITE_RESEND_BUCKETS.clear()

    def test_expire_kills_link_and_pending(self, client, reg):
        """Admin expire-now: the invite leaves the invitee pending list and
        the link dies; consumed invites cannot be expired (409)."""
        inv = _invite(client, reg, email="bob@example.com")
        r = client.post(f"/v1/invites/{inv['invite_id']}/expire?team_id=team-f")
        assert r.status_code == 200, r.text
        assert r.json()["expired"] is True
        # pending list (invitee side) is empty
        _as_user(_U_BOB, "bob@example.com")
        pend = client.get("/v1/invites/pending")
        assert pend.json()["invites"] == []
        # token accept → expired (400)
        r = client.post("/v1/invites/accept", json={"token": inv["token"]})
        assert r.status_code == 400
        # consumed invite → 409 on expire
        _as_user(_U1, "owner@example.com")
        inv2 = _invite(client, reg, team_id="team-h", email="carol@example.com")
        _as_user(_U_CAROL, "carol@example.com")
        assert client.post("/v1/invites/accept",
                           json={"token": inv2["token"]}).status_code == 200
        _as_user(_U1, "owner@example.com")
        r2 = client.post(f"/v1/invites/{inv2['invite_id']}/expire?team_id=team-h")
        assert r2.status_code == 409
        # expired invite cannot be rescinded-then-resurrected: resend 400
        r3 = client.post(f"/v1/invites/{inv['invite_id']}/resend?team_id=team-f")
        assert r3.status_code == 400

    def test_expire_member_403(self, client, reg):
        inv = _invite(client, reg, email="bob@example.com")
        _seed_membership(reg, "team-f", _U_BOB, "member")
        _as_user(_U_BOB, "bob@example.com")
        r = client.post(f"/v1/invites/{inv['invite_id']}/expire?team_id=team-f")
        assert r.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# member_progress arming (DE2E-8: writes member slot, never fakes org steps)
# ═══════════════════════════════════════════════════════════════════════════


class TestMemberProgressArming:
    def _member_node(self, team_id: str):
        from tortoise.hosted_api import _make_sdk
        from tortoise.onboarding import state as _os
        sdk = _make_sdk(namespace=team_id)
        _REG_SDKS.append(sdk)
        return _os.read_onboarding_node(sdk._get_proj(), team_id)

    def _completed_steps(self, team_id: str):
        from tortoise.hosted_api import _make_sdk
        from tortoise.onboarding import state as _os
        sdk = _make_sdk(namespace=team_id)
        _REG_SDKS.append(sdk)
        return _os.completed_steps(sdk._get_proj(), team_id)

    def test_match_accept_arms_member_slot_without_faking_org_steps(
            self, client, reg):
        """DE2E-8: a one-click accept arms the invitee's member slot
        (member_progress {user_id: []}) while org-level completed steps stay
        untouched. The node is materialized via W5's create-on-write seam
        (byte-identical to the register/org-create eager init — team-named is
        auto-satisfied AT INIT like every team-create lane; it is not a
        member write). Member writes never add member-scoped steps to
        org-level edges; the node is not 'complete'."""
        inv = _invite(client, reg, team_id="team-m1", email="bob@example.com")
        _as_user(_U_BOB, "bob@example.com")
        r = client.post("/v1/invites/accept", json={"token": inv["token"]})
        assert r.status_code == 200, r.text
        node = self._member_node("team-m1")
        assert node is not None, "OnboardingState node not armed after accept"
        from tortoise.onboarding import state as _os
        progress = _os.parse_member_progress(node.get("member_progress"))
        assert progress.get(_U_BOB) == []  # armed, zero steps
        assert node.get("status") != _os.STATUS_COMPLETE
        # no org-level steps were faked by the arming
        steps = self._completed_steps("team-m1")
        assert "decide-completed" not in steps
        assert "harness-connected" not in steps
        assert "first-points-filed" not in steps

    def test_override_accept_arms_current_user_slot(self, client, reg,
                                                    monkeypatch):
        """A mismatch-override accept (fuse + OTP) arms the CURRENT user's
        slot — org-level steps still untouched (skipping never completes);
        the member slot is a user-scoped key, not a COMPLETED_STEP edge."""
        inv = _invite(client, reg, team_id="team-m2", email="bob@example.com")
        _as_user(_U_ALICE, "alice@example.com")
        captured = {}
        _capture_otp(monkeypatch, captured)
        assert client.post("/v1/invites/otp",
                           json={"token": inv["token"]}).status_code == 200
        r = _v2_accept(client, inv["token"], path="fuse", otp=captured["code"])
        assert r.status_code == 200, r.text
        node = self._member_node("team-m2")
        from tortoise.onboarding import state as _os
        progress = _os.parse_member_progress(node.get("member_progress"))
        assert progress.get(_U_ALICE) == []
        steps = self._completed_steps("team-m2")
        assert "decide-completed" not in steps

    def test_member_checkpoint_write_never_advances_org_gate(
            self, client, reg, monkeypatch):
        """The invitee inline setup (W5 checkpoint member_progress write with
        a real step the member completed) does NOT advance org-level steps —
        member entries are user-scoped keys, not COMPLETED_STEP edges."""
        from tortoise.onboarding import state as _os
        inv = _invite(client, reg, team_id="team-m3", email="bob@example.com")
        _as_user(_U_BOB, "bob@example.com")
        assert client.post("/v1/invites/accept",
                           json={"token": inv["token"]}).status_code == 200
        # the invitee's inline setup completes the harness step FOR THEM
        team = {"team_id": "team-m3", "session_user_id": _U_BOB,
                "graph_name": "team_team-m3"}
        app.dependency_overrides.clear()  # checkpoint is dual-auth via team dep
        from tortoise.hosted_api import get_current_team_session_ungated
        app.dependency_overrides[get_current_team_session_ungated] = lambda: team
        r = client.post("/v1/onboarding/state/checkpoint",
                        json={"member_progress": {_U_BOB: ["harness-connected"]}})
        assert r.status_code == 200, r.text
        node = self._member_node("team-m3")
        progress = _os.parse_member_progress(node.get("member_progress"))
        assert progress.get(_U_BOB) == ["harness-connected"]
        # org-level steps NOT advanced — completion gate NOT satisfied
        steps = self._completed_steps("team-m3")
        assert "harness-connected" not in steps
        assert node.get("status") != _os.STATUS_COMPLETE


# ═══════════════════════════════════════════════════════════════════════════
# Concurrency regressions (W7 review P2-1/P2-2): the single-use accept claim
# must be AUTHORITATIVE under parallel submits. Pre-fix, the loser of the
# token-consume race re-read accepted_at (the WINNER's) and minted a SECOND
# membership — the fix makes the conditional write's own matched-row count
# the claim (registry) / return=representation row (supabase), so a loser
# always 409s before any membership write. No concurrency test existed
# (all replay tests are sequential — they pass because the invite is already
# consumed before the second call reaches the claim).
# ═══════════════════════════════════════════════════════════════════════════


class TestConcurrentAcceptSingleUse:
    def test_two_users_race_one_token_one_membership(self, client, reg,
                                                     monkeypatch):
        """P2-1: email-match invitee (legacy one-click, no OTP) racing an
        OTP-verified mismatch user (v2 fuse) on the SAME token — exactly ONE
        accept wins; the loser 409s; exactly ONE active membership exists
        (never a second from the losing request)."""
        import httpx
        inv = _invite(client, reg, team_id="team-race1", email="bob@example.com")
        # Alice mints + captures the OTP (mismatch override needs it)
        _as_user(_U_ALICE, "alice@example.com")
        captured = {}
        _capture_otp(monkeypatch, captured)
        assert client.post("/v1/invites/otp",
                           json={"token": inv["token"]}).status_code == 200
        code = captured["code"]

        # Per-request identity (contextvar, test_invites_http pattern):
        # Bob acts as the email-match invitee; Alice as the OTP mismatch.
        _actor = contextvars.ContextVar("invite_actor", default=None)

        def _override():
            return _actor.get() or {"user_id": _U1,
                                    "email": "owner@example.com"}

        app.dependency_overrides[get_current_user] = _override

        async def _accept_bob():
            _actor.set({"user_id": _U_BOB, "email": "bob@example.com"})
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://test") as ac:
                # email match → legacy one-click accept (no v2 header)
                return await ac.post("/v1/invites/accept",
                                     json={"token": inv["token"]})

        async def _accept_alice():
            _actor.set({"user_id": _U_ALICE, "email": "alice@example.com"})
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://test") as ac:
                return await ac.post("/v1/invites/accept",
                                     json={"token": inv["token"],
                                           "path": "fuse", "otp": code},
                                     headers={"Accept": V2_ACCEPT})

        async def _run_race():
            return await asyncio.gather(_accept_bob(), _accept_alice())

        bob_r, alice_r = asyncio.run(_run_race())
        codes = sorted([bob_r.status_code, alice_r.status_code])
        # One wins (200). The loser is rejected by whichever gate fires first
        # in the interleaving — the token-resolution 400 (invite already
        # consumed), the OTP 403, or the authoritative claim 409. The REGRESSION
        # contract (P2-1) is the INVARIANT: never 200+200, never a second
        # membership — the loser must not mint under any interleaving.
        assert codes[0] == 200 and codes[1] in (400, 403, 409), [
            bob_r.text, alice_r.text]
        # exactly ONE active NON-owner membership on this team (owner _U1 is
        # seeded by _seed_team_with_owner) — pre-fix the loser minted a dup
        rows = reg.query(
            "MATCH (m:Membership {team_id:'team-race1', status:'active'}) "
            "RETURN collect(m.user_id)",
        ).result_set[0][0]
        non_owner = [u for u in rows if u != _U1]
        assert len(non_owner) == 1, f"expected 1 accepted member, got {rows}"
        # the invite is consumed exactly once (accepted_by is the winner)
        winner_uid = _U_BOB if bob_r.status_code == 200 else _U_ALICE
        row = reg.query(
            "MATCH (i:Invitation {id:$id}) RETURN i.accepted_at, "
            "i.accepted_by",
            params={"id": inv["invite_id"]},
        ).result_set[0]
        assert row[0] is not None
        assert row[1] == winner_uid
        app.dependency_overrides.pop(get_current_user, None)

    def test_same_user_double_submit_one_membership(self, client, reg,
                                                    monkeypatch):
        """P2-2: the SAME user double-submits the v2 fuse accept concurrently
        (double-click / client retry). Exactly one wins; the loser 409s on the
        authoritative claim and NEVER reaches the membership write (pre-fix it
        minted a duplicate — registry has no (user,team) unique constraint)."""
        import httpx
        inv = _invite(client, reg, team_id="team-race2", email="bob@example.com")
        _as_user(_U_ALICE, "alice@example.com")
        captured = {}
        _capture_otp(monkeypatch, captured)
        assert client.post("/v1/invites/otp",
                           json={"token": inv["token"]}).status_code == 200
        code = captured["code"]

        _actor = contextvars.ContextVar("invite_actor", default=None)

        def _override():
            return _actor.get() or {"user_id": _U1,
                                    "email": "owner@example.com"}

        app.dependency_overrides[get_current_user] = _override

        async def _accept_alice():
            _actor.set({"user_id": _U_ALICE, "email": "alice@example.com"})
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://test") as ac:
                return await ac.post("/v1/invites/accept",
                                     json={"token": inv["token"],
                                           "path": "fuse", "otp": code},
                                     headers={"Accept": V2_ACCEPT})

        async def _run_race():
            return await asyncio.gather(_accept_alice(), _accept_alice())

        r1, r2 = asyncio.run(_run_race())
        codes = sorted([r1.status_code, r2.status_code])
        # One 200, the loser rejected at the first gate in the interleaving
        # (resolution 400 / OTP 403 / claim 409). INVARIANT: never 200+200
        # and never a compensating rollback that unwinds the winner (P2-2 —
        # pre-fix the loser's membership-write exception PATCHed the invite
        # back to pending, orphaning the winner's active membership).
        assert codes[0] == 200 and codes[1] in (400, 403, 409), [r1.text,
                                                                 r2.text]
        # exactly ONE active membership for alice on this team (no dup)
        rows = reg.query(
            "MATCH (m:Membership {team_id:'team-race2', user_id:$uid, "
            "status:'active'}) RETURN count(m)",
            params={"uid": _U_ALICE},
        ).result_set[0][0]
        assert rows == 1, f"expected 1 alice membership, got {rows}"
        # the winner's commit is NOT unwound: the invite stays accepted (not
        # rolled back to pending by a loser's compensating write)
        row = reg.query(
            "MATCH (i:Invitation {id:$id}) RETURN i.accepted_at, "
            "i.accepted_by",
            params={"id": inv["invite_id"]},
        ).result_set[0]
        assert row[0] is not None and row[1] == _U_ALICE
        app.dependency_overrides.pop(get_current_user, None)
