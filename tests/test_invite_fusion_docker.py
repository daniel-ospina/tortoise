"""#2003 (W7) — invite-accept fusion DE2E-7/8 docker-lane journey tests.

Docker-lane (TORTOISE_DB_URI): real FalkorDB graph semantics (server-mode
eager-init Cypher + keyed-MERGE writers — the embedded redislite lane cannot
satisfy these, #1997 tier-2 regression). URI-less runs (tier-2 embedded
legs / carve-out) SKIP at module level — mirror tests/test_onboarding_state_split.py.

Journey under test (epic DE2E-8 surface 12):
- register a fresh owner (account leg) → invite a second email → mismatch
  override accept via OTP (DE2E-7 mechanics on the real graph) →
  consumed-token replay is idempotent → member_progress arming writes the
  user-scoped slot WITHOUT advancing org-level steps / faking completion.
"""
from __future__ import annotations

import asyncio
import os
import uuid

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import pytest

# docker-lane gate (epic #1647 P4 / #1997): URI-less embedded legs cannot run
# these server-mode graph assertions — skip cleanly instead of failing.
from tortoise.config import is_db_uri as _is_db_uri

if not _is_db_uri(os.environ.get("TORTOISE_DB_URI")):
    pytest.skip("docker-lane invite-fusion tests require TORTOISE_DB_URI "
                "(tier-2 embedded legs skip)", allow_module_level=True)

from fastapi.testclient import TestClient

from tortoise import email_notify
from tortoise.hosted_api import _make_sdk, _registry_mismatch_accept_v2, app, get_current_user
from tortoise.onboarding import state as _os

# real-JWT-shaped uuid subjects (#1719)
_U_OWNER = "9f2c1a40-0000-4a00-8000-000000000101"
_U_INVITEE = "9f2c1a40-0000-4a00-8000-000000000102"
_U_RACER_B = "9f2c1a40-0000-4a00-8000-000000000103"
_U_RACER_C = "9f2c1a40-0000-4a00-8000-000000000104"

V2_ACCEPT = "application/vnd.tortoise.onboarding+json;version=2"


@pytest.fixture
def client():
    """Registry-mode TestClient on the docker lane (env URI)."""
    with TestClient(app) as tc:
        yield tc


def _suffix() -> str:
    return uuid.uuid4().hex[:10]


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_secret_key_123")
    monkeypatch.setenv("EMAIL_LINK_BASE_URL", "https://tortoise.premiselabs.co")
    email_notify._skip_logged.clear()


def _invitee_email() -> str:
    return f"invitee-{_suffix()}@example.com"


class TestFusionDockerJourney:
    def test_de2e8_atomic_accept_arms_member_progress(
            self, client, env, monkeypatch):
        """Register (account) → invite → one-click accept (atomic: token
        consumed + membership in the SAME request) → member slot armed,
        org-level steps untouched, replay idempotent."""
        owner_email = f"owner-{_suffix()}@example.com"
        r = client.post("/v1/register",
                        json={"email": owner_email, "password": "password123"})
        assert r.status_code == 200, r.text
        team_id = r.json()["team_id"]

        # owner Membership is required for invite RBAC — the register lane
        # keys the team by email (no membership row), so seed it directly on
        # the shared control graph.
        reg = _make_sdk(namespace="registry")._get_registry()
        reg.query(
            "CREATE (m:Membership {user_id:$uid, team_id:$tid, role:'owner', "
            "status:'active', created_at:'2026-09-01T00:00:00+00:00'})",
            params={"uid": _U_OWNER, "tid": team_id},
        )
        # register provisions the team at tier='free' — invites need the Team
        # tier (docker lane is registry mode; the conftest provision fixture
        # does the same tier bump for its teams).
        reg.query("MATCH (t:Team {id:$id}) SET t.tier = 'team', "
                  "t.max_users = 3",
                  params={"id": team_id})
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _U_OWNER, "email": owner_email,
        }
        inv_email = _invitee_email()
        r = client.post("/v1/invites",
                        json={"team_id": team_id, "email": inv_email})
        assert r.status_code == 200, r.text
        token = r.json()["token"]

        # Atomic accept as the invitee (email match — one action, no
        # intermediate create-then-accept state).
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _U_INVITEE, "email": inv_email,
        }
        r = client.post("/v1/invites/accept", json={"token": token})
        assert r.status_code == 200, r.text
        assert r.json() == {"team_id": team_id, "role": "member"}
        # membership exists in the control graph
        rows = reg.query(
            "MATCH (m:Membership {team_id:$tid, user_id:$uid, status:'active'}) "
            "RETURN count(m)",
            params={"tid": team_id, "uid": _U_INVITEE},
        ).result_set
        assert rows[0][0] == 1

        # member slot armed: node exists (create-on-write) + user-scoped
        # member_progress {user_id: []} — never org-level steps.
        proj = _make_sdk(namespace=team_id)._get_proj()
        node = _os.read_onboarding_node(proj, team_id)
        assert node is not None, "OnboardingState node not armed after accept"
        progress = _os.parse_member_progress(node.get("member_progress"))
        assert progress.get(_U_INVITEE) == []
        assert node.get("status") == _os.STATUS_ACTIVE
        # the org's own register-time edge (team-named) is the ONLY org-level
        # step — the accept/member write never faked the rest.
        steps = _os.completed_steps(proj, team_id)
        assert set(steps) <= {"team-named"}

        # consumed-token replay → idempotent failure, no double membership
        r2 = client.post("/v1/invites/accept", json={"token": token})
        assert r2.status_code == 400
        rows = reg.query(
            "MATCH (m:Membership {team_id:$tid, user_id:$uid, status:'active'}) "
            "RETURN count(m)",
            params={"tid": team_id, "uid": _U_INVITEE},
        ).result_set
        assert rows[0][0] == 1

    def test_de2e7_mismatch_override_with_otp_on_real_graph(
            self, client, env, monkeypatch):
        """3-path discovery → OTP send (captured via the email seam) → fuse
        override with OTP accepts under the CURRENT account and records the
        mismatch + proof on the invitation."""
        owner_email = f"owner2-{_suffix()}@example.com"
        r = client.post("/v1/register",
                        json={"email": owner_email, "password": "password123"})
        assert r.status_code == 200, r.text
        team_id = r.json()["team_id"]
        reg = _make_sdk(namespace="registry")._get_registry()
        reg.query(
            "CREATE (m:Membership {user_id:$uid, team_id:$tid, role:'owner', "
            "status:'active', created_at:'2026-09-01T00:00:00+00:00'})",
            params={"uid": _U_OWNER, "tid": team_id},
        )
        # register provisions the team at tier='free' — invites need the Team
        # tier (docker lane is registry mode; the conftest provision fixture
        # does the same tier bump for its teams).
        reg.query("MATCH (t:Team {id:$id}) SET t.tier = 'team', "
                  "t.max_users = 3",
                  params={"id": team_id})
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _U_OWNER, "email": owner_email,
        }
        inv_email = _invitee_email()
        r = client.post("/v1/invites",
                        json={"team_id": team_id, "email": inv_email})
        assert r.status_code == 200, r.text
        inv = r.json()

        # a DIFFERENT logged-in account clicks the link → opted-in mismatch
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _U_INVITEE, "email": f"other-{_suffix()}@example.com",
        }
        # 3-path discovery (never silent)
        r = client.post("/v1/invites/accept", json={"token": inv["token"]},
                        headers={"Accept": V2_ACCEPT})
        assert r.status_code == 409, r.text
        choice = r.json()["detail"]["choice"]
        assert choice["default_path"] == "fuse"
        assert choice["invited_email"] == inv_email
        # fuse without OTP → blocked
        r = client.post("/v1/invites/accept",
                        json={"token": inv["token"], "path": "fuse"},
                        headers={"Accept": V2_ACCEPT})
        assert r.status_code == 403
        assert r.json()["detail"]["error_code"] == "invite_mismatch_otp_required"

        # send the OTP to the INVITEE mailbox (captured via the email seam)
        captured = {}

        def fake_send(team_name, invitee_email, code, on_sent=None):
            captured.update(code=code, email=invitee_email)

        monkeypatch.setattr(email_notify, "send_otp_email", fake_send)
        r = client.post("/v1/invites/otp", json={"token": inv["token"]})
        assert r.status_code == 200, r.text
        assert captured["email"] == inv_email

        # fuse + OTP → accepted under the current account
        r = client.post("/v1/invites/accept",
                        json={"token": inv["token"], "path": "fuse",
                              "otp": captured["code"]},
                        headers={"Accept": V2_ACCEPT})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["accepted_via"] == "fuse"
        assert body["mismatch"] == {"invited_email": inv_email, "recorded": True}
        rows = reg.query(
            "MATCH (m:Membership {team_id:$tid, user_id:$uid, status:'active'}) "
            "RETURN count(m)",
            params={"tid": team_id, "uid": _U_INVITEE},
        ).result_set
        assert rows[0][0] == 1
        # invite records the override — never silent
        row = reg.query(
            "MATCH (i:Invitation {id:$id}) RETURN i.accepted_via, "
            "i.accepted_mismatch, i.fused_from_email, i.otp_verified_at",
            params={"id": inv["invite_id"]},
        ).result_set[0]
        assert row[0] == "fuse" and row[1] is True
        assert row[2] == inv_email and row[3] is not None

        # legacy (no v2 header) replay → byte-unchanged 403 mismatch... the
        # invite is consumed now — replay is the consumed-token 400 (not a
        # double membership).
        r = client.post("/v1/invites/accept", json={"token": inv["token"]})
        assert r.status_code == 400

    def test_legacy_403_byte_unchanged_no_optin(self, client, env):
        """DE2E-7: no v2 header → the pre-W7 403 verbatim on the real lane."""
        owner_email = f"owner3-{_suffix()}@example.com"
        r = client.post("/v1/register",
                        json={"email": owner_email, "password": "password123"})
        assert r.status_code == 200, r.text
        team_id = r.json()["team_id"]
        reg = _make_sdk(namespace="registry")._get_registry()
        reg.query(
            "CREATE (m:Membership {user_id:$uid, team_id:$tid, role:'owner', "
            "status:'active', created_at:'2026-09-01T00:00:00+00:00'})",
            params={"uid": _U_OWNER, "tid": team_id},
        )
        # register provisions the team at tier='free' — invites need the Team
        # tier (docker lane is registry mode; the conftest provision fixture
        # does the same tier bump for its teams).
        reg.query("MATCH (t:Team {id:$id}) SET t.tier = 'team', "
                  "t.max_users = 3",
                  params={"id": team_id})
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _U_OWNER, "email": owner_email,
        }
        inv_email = _invitee_email()
        r = client.post("/v1/invites",
                        json={"team_id": team_id, "email": inv_email})
        assert r.status_code == 200, r.text
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _U_INVITEE,
            "email": f"stranger-{_suffix()}@example.com",
        }
        r = client.post("/v1/invites/accept", json={"token": r.json()["token"]})
        assert r.status_code == 403
        assert r.json()["detail"] == "Invite email does not match this account"


class TestConcurrentClaimRace:
    """P2-1/P2-2 concurrency regression (review fixes at 424fc2df~..): the
    executor's single-use claim is authoritative because the conditional
    SET's OWN matched-row count is the claim (RETURN count(i)) — a loser who
    races a winner that already consumed the token sees count(i)=0 and 409s
    BEFORE any membership write. On THIS lane the executor's critical
    section is synchronous (atomic per coroutine, cf. #1965), so the loser
    is rejected at whichever gate fires first in the interleaving (OTP 403
    after the winner's commit, or the claim 409) — the INVARIANT is exactly
    one winner and exactly one membership under every interleaving. Seam
    level on the real graph: two users share ONE outstanding OTP code (OTP
    verify is NON-consuming)."""

    def _seed(self, client, reg):
        owner_email = f"owner-race-{_suffix()}@example.com"
        r = client.post("/v1/register",
                        json={"email": owner_email, "password": "password123"})
        assert r.status_code == 200, r.text
        team_id = r.json()["team_id"]
        reg.query(
            "CREATE (m:Membership {user_id:$uid, team_id:$tid, role:'owner', "
            "status:'active', created_at:'2026-09-01T00:00:00+00:00'})",
            params={"uid": _U_OWNER, "tid": team_id},
        )
        reg.query("MATCH (t:Team {id:$id}) SET t.tier = 'team', "
                  "t.max_users = 5",
                  params={"id": team_id})
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _U_OWNER, "email": owner_email,
        }
        inv_email = f"invitee-race-{_suffix()}@example.com"
        r = client.post("/v1/invites",
                        json={"team_id": team_id, "email": inv_email})
        assert r.status_code == 200, r.text
        return team_id, inv_email, r.json()

    def test_loser_409_no_dup_membership(self, client, env, monkeypatch):
        """Two DIFFERENT users race one token with the same valid OTP — one
        membership, loser 409 (never 200+200 / never a duplicate)."""
        reg = _make_sdk(namespace="registry")._get_registry()
        team_id, inv_email, inv = self._seed(client, reg)

        # a different logged-in account mints the OTP (sent to the invitee
        # mailbox — captured via the email seam, like the DE2E-7 test)
        captured = {}

        def fake_send(team_name, invitee_email, code, on_sent=None):
            captured.update(code=code, email=invitee_email)

        monkeypatch.setattr(email_notify, "send_otp_email", fake_send)
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _U_INVITEE, "email": f"other-{_suffix()}@example.com",
        }
        r = client.post("/v1/invites/otp", json={"token": inv["token"]})
        assert r.status_code == 200, r.text
        assert captured["email"] == inv_email
        code = captured["code"]

        # Two racers (both CONTROL the mailbox — both hold the same code).
        # OTP verify is non-consuming, so both pass the OTP gate; they then
        # serialize at _invite_team_lock. The loser's conditional claim SET
        # matches 0 rows (winner already transitioned pending→accepted) → 409.
        sdk = _make_sdk(namespace="registry")
        invite_row = {"id": inv["invite_id"], "team_id": team_id,
                      "email": inv_email, "role": "member"}

        async def _race():
            async def _accept(uid, email):
                return await _registry_mismatch_accept_v2(
                    sdk, invite_row, {"user_id": uid, "email": email},
                    "fuse", code)
            return await asyncio.gather(
                _accept(_U_RACER_B, f"racer-b-{_suffix()}@example.com"),
                _accept(_U_RACER_C, f"racer-c-{_suffix()}@example.com"),
                return_exceptions=True,
            )

        results = asyncio.run(_race())
        # INVARIANT (holds under every interleaving): exactly ONE winner and
        # exactly ONE membership. On this lane the executor's critical section
        # is synchronous (atomic per coroutine, cf. #1965 test comment) — so
        # the loser is rejected at the OTP gate (403 no_otp, the code is
        # consumed by the winner's commit) rather than reaching the claim
        # 409. The claim rowcount (RETURN count(i)) is the defense-in-depth
        # for lanes with real awaits between verify and claim (supabase).
        ok = [r for r in results if isinstance(r, dict)]
        errs = [r for r in results if isinstance(r, Exception)]
        assert len(ok) == 1, f"expected exactly 1 winner, got {results}"
        assert len(errs) == 1, f"expected exactly 1 loser, got {results}"
        from fastapi import HTTPException as _HTTPException
        assert isinstance(errs[0], _HTTPException) and \
            errs[0].status_code in (403, 409), f"loser: {errs[0]}"
        # exactly ONE active membership for the racers (no dup from loser)
        rows = reg.query(
            "MATCH (m:Membership {team_id:$tid, status:'active'}) "
            "WHERE m.user_id = $b OR m.user_id = $c "
            "RETURN collect(m.user_id)",
            params={"tid": team_id, "b": _U_RACER_B, "c": _U_RACER_C},
        ).result_set[0][0]
        assert len(rows) == 1, f"expected 1 racer membership, got {rows}"
        # invite consumed exactly once by the winner (accepted_by matches)
        winner_uid = rows[0]
        row = reg.query(
            "MATCH (i:Invitation {id:$id}) RETURN i.accepted_at, "
            "i.accepted_by, i.accepted_via",
            params={"id": inv["invite_id"]},
        ).result_set[0]
        assert row[0] is not None
        assert row[1] == winner_uid and row[2] == "fuse"
        # loser was NOT written (accepted_via/otp state belongs to winner only)
        app.dependency_overrides.pop(get_current_user, None)

    def test_deterministic_loser_claim_409_registry(self, client, env,
                                                    monkeypatch):
        """The registry executor's claim rowcount, pinned deterministically
        (the seam race above can't reach the claim on this lane because the
        executor's critical section is synchronous/atomic per coroutine —
        the loser is always rejected at the OTP gate first). Here a winner
        commits BETWEEN the loser's OTP verify and the loser's claim SET by
        injecting at the reg.query boundary: the loser's conditional claim
        then matches 0 rows → RETURNS count(i)=0 → 409, never a second
        membership. Pre-fix (SET without rowcount + re-read of accepted_at)
        the loser saw the winner's accepted_at and minted a dup — this test
        FAILS pre-fix."""
        from datetime import UTC
        from datetime import datetime as _dt
        sdk = _make_sdk(namespace="registry")
        reg = sdk._get_registry()
        team_id, inv_email, inv = self._seed(client, reg)

        # mint ONE outstanding code (captured via the email seam)
        captured = {}

        def fake_send(team_name, invitee_email, code, on_sent=None):
            captured.update(code=code, email=invitee_email)

        monkeypatch.setattr(email_notify, "send_otp_email", fake_send)
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _U_INVITEE, "email": f"other-{_suffix()}@example.com",
        }
        r = client.post("/v1/invites/otp", json={"token": inv["token"]})
        assert r.status_code == 200, r.text
        code = captured["code"]

        orig_query = reg.query
        winner_uid = _U_RACER_C
        loser_uid = _U_RACER_B
        now_iso = _dt.now(UTC).isoformat()
        fired = {"win": False}

        def _wrapped(q, *, params=None, **kw):
            # Fire the winner's commit at the LOSER's claim SET (the only
            # v2 claim write — matches the otp-consuming conditional SET).
            if (not fired["win"]
                    and "RETURN count(i)" in q
                    and "i.otp_verified_at = $now" in q):
                fired["win"] = True
                orig_query(
                    "MATCH (i:Invitation {id:$id}) "
                    "WHERE i.accepted_at IS NULL "
                    "AND (i.status IS NULL OR i.status = 'pending') "
                    "SET i.accepted_at = $now, i.accepted_by = $uid, "
                    "i.accepted_via = 'fuse', i.accepted_mismatch = true, "
                    "i.fused_from_email = $fused, i.otp_hash = null, "
                    "i.otp_expires_at = null, i.otp_attempts = 0, "
                    "i.otp_verified_at = $now, i.otp_verified_by = $uid "
                    "RETURN count(i)",
                    params={"id": inv["invite_id"], "now": now_iso,
                            "uid": winner_uid, "via": "fuse",
                            "fused": inv_email},
                )
                sdk.membership_create(team_id, winner_uid, "member")
            return orig_query(q, params=params, **kw)

        monkeypatch.setattr(reg, "query", _wrapped)
        invite_row = {"id": inv["invite_id"], "team_id": team_id,
                      "email": inv_email, "role": "member"}

        async def _loser_accept():
            return await _registry_mismatch_accept_v2(
                sdk, invite_row, {"user_id": loser_uid,
                                  "email": f"loser-{_suffix()}@example.com"},
                "fuse", code)

        from fastapi import HTTPException as _HTTPException
        try:
            asyncio.run(_loser_accept())
            raise AssertionError("loser must NOT succeed (claim lost)")
        except _HTTPException as e:
            assert e.status_code == 409, e
        # loser did NOT mint; winner has exactly one membership
        rows = reg.query(
            "MATCH (m:Membership {team_id:$tid, status:'active'}) "
            "WHERE m.user_id = $b OR m.user_id = $c "
            "RETURN collect(m.user_id)",
            params={"tid": team_id, "b": loser_uid, "c": winner_uid},
        ).result_set[0][0]
        assert rows == [winner_uid], f"expected only winner, got {rows}"
        # invite carries the winner's proof (never clobbered by the loser)
        row = reg.query(
            "MATCH (i:Invitation {id:$id}) RETURN i.accepted_by, "
            "i.accepted_via, i.otp_verified_by",
            params={"id": inv["invite_id"]},
        ).result_set[0]
        assert row[0] == winner_uid
        assert row[1] == "fuse" and row[2] == winner_uid
        assert fired["win"], "winner-injection never fired — test is vacuous"
        app.dependency_overrides.pop(get_current_user, None)
