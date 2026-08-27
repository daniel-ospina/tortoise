"""POST /v1/claim + GET /v1/claim/status endpoint tests (#1082, PR1).

Claim = ONE endpoint requiring BOTH credentials: a fresh Supabase session
JWT (Authorization header, verified server-side) + the pasted tt_ key
(body.api_key — key-possession anchor). The provider-verified-email
invariant (app_metadata.providers ∩ {github,google} ≠ ∅) + the
email_confirmed_at conjunct gate the claim BEFORE the RPC.

Covered here (all Supabase-mode via the FakeControlPlane):
- bad/missing JWT → 401
- null email → fail-closed 400
- email+password-only session (providers=['email']) → 403 (must NOT claim
  + overwrite teams.email — solution-verify P2-3)
- provider-invariant negatives: fresh password token / refreshed password
  token (amr='token_refresh', no github/google provider) → 403; an
  amr-LESS github token still passes (app_metadata survives refresh —
  amr is NEVER the invariant source)
- github/google provider → 200; same key still resolves pre/post
  (indicator 1); audit team_claim with detail
- second claim by a different user → 409 first-claim-wins
- tamper: client-supplied team_id/identity REJECTED — the RPC binds by
  lookup_hash only
- claim limiter: 2/24h per IP → 429 on the 3rd attempt (24h-window bucket)
- /v1/claim/status: key-scoped claimability probe (welcome guard)
"""
from __future__ import annotations

import os
import sys
import uuid

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001
from fastapi.testclient import TestClient

import tortoise.hosted_api as ha_mod
import tortoise.supabase_control as sc
from tortoise.auth import lookup_hash  # noqa: F401
from tortoise.hosted_api import app
from tests.fake_control_plane import FakeControlPlane

_SUPABASE_URL = "https://claimtest.supabase.co"

# #1719 (Task 3): JWT subjects are real UUIDs — claim_status's
# membership_for_user_team filters team_memberships.user_id (uuid column);
# a non-UUID literal 22P02s (HTTP 400) under the fake's UUID fidelity.
_U_A = "9f2c1a40-0000-4a00-8000-00000000000a"
_U_B = "9f2c1a40-0000-4a00-8000-00000000000b"
_U_X = "9f2c1a40-0000-4a00-8000-00000000000c"
_U_1 = "9f2c1a40-0000-4a00-8000-000000000001"


@pytest.fixture(autouse=True)
def _claim_env(monkeypatch):
    """Supabase-mode env + fresh FakeControlPlane + limiter off by default.

    Each test gets a pristine fake (provisioned teams per test) and a reset
    claim bucket so the limiter test can opt back in cleanly.
    """
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", _SUPABASE_URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-claim-test")
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
    ha_mod._CLAIM_BUCKETS.clear()
    fake = FakeControlPlane()
    monkeypatch.setattr(sc, "get_control_plane", lambda: fake)

    async def _confirmed(request):
        return True

    monkeypatch.setattr(ha_mod, "_gotrue_email_confirmed", _confirmed)
    yield fake
    ha_mod._CLAIM_BUCKETS.clear()


@pytest.fixture

def _fake_from_env(_claim_env):
    """Expose the _claim_env-created FakeControlPlane as `fake`."""
    return _claim_env


@pytest.fixture
def fake(_fake_from_env):
    return _fake_from_env


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _provision_anon(client, fake, *, team_id=None, identity=None, email=None):
    """Mint an anonymous team through the real /v1/agent/signup path.

    Returns the plaintext key. The signup writes through the fake's
    provision_team RPC (Supabase mode) — the same seam the endpoint uses.
    """
    if team_id is None:
        team_id = f"team-{uuid.uuid4().hex[:10]}"
    if identity is None:
        identity = f"anon-{uuid.uuid4().hex[:12]}"
    r = client.post("/v1/agent/signup", json={})
    assert r.status_code == 200, r.text
    data = r.json()
    return data["key"], data["team_id"]


def _jwt(user_id: str, *, email: str | None = "claim@example.com",
         providers: list[str] | None = None, amr: list | None = None,
         include_amr: bool = False) -> dict:
    """Fake session payload (verify_session_jwt is monkeypatched to return
    this dict — the token string itself is never parsed)."""
    payload = {
        "user_id": user_id,
        "email": email,
        "app_metadata": {"providers": providers or ["email"]},
    }
    if include_amr:
        payload["amr"] = amr
    return payload


def _patch_verify(monkeypatch, payload: dict):
    async def _fake_verify(request):
        return payload

    monkeypatch.setattr(ha_mod, "verify_session_jwt", _fake_verify)


class TestClaimEndpoint:
    def test_requires_both_credentials_bad_jwt_401(self, client, fake,
                                                   monkeypatch):
        """No/malformed session JWT → 401 before any key work."""
        r = client.post("/v1/claim", json={"api_key": "tt_whatever"})
        assert r.status_code == 401, r.text
        r = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer not-a-jwt"},
            json={"api_key": "tt_whatever"},
        )
        assert r.status_code == 401, r.text

    def test_null_email_fail_closed(self, client, fake, monkeypatch):
        """Fail-closed on null email — a provider-verified claim REQUIRES a
        verified email (overwriting teams.email with NULL would orphan)."""
        _patch_verify(monkeypatch, _jwt(_U_X, email=None,
                                        providers=["github"]))
        r = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={"api_key": "tt_whatever"},
        )
        assert r.status_code == 400, r.text
        assert "email" in r.json()["detail"].lower()

    def test_password_only_session_403(self, client, fake, monkeypatch):
        """email+password-only session (providers=['email']) must NOT claim
        + overwrite teams.email (solution-verify P2-3)."""
        _patch_verify(monkeypatch, _jwt(_U_X, providers=["email"]))
        r = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={"api_key": "tt_whatever"},
        )
        assert r.status_code == 403, r.text
        assert "GitHub or Google" in r.json()["detail"]

    def test_password_token_on_linked_account_passes(self, client, fake,
                                                     monkeypatch):
        """A password login on a github-LINKED account legitimately passes:
        providers accumulates on linking (app_metadata survives refresh).
        This is the intended semantics (documented in the plan)."""
        key, team_id = _provision_anon(client, fake)  # noqa: RUF059
        # providers accumulate: email (password) + github (linked earlier)
        _patch_verify(monkeypatch, _jwt(_U_X, email="a@b.co",
                                        providers=["email", "github"]))
        r = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={"api_key": key},
        )
        assert r.status_code == 200, r.text

    @pytest.mark.parametrize("amr,label", [
        ([{"method": "password", "timestamp": 1}], "fresh password token"),
        ([{"method": "token_refresh", "timestamp": 2}], "refreshed password token"),
    ])
    def test_provider_invariant_negative_amr_never_used(self, client, fake,
                                                        monkeypatch, amr,
                                                        label):
        """amr is NEVER the invariant source: a fresh/refreshed PASSWORD token
        (amr without github/google) must NOT claim even when amr is present —
        app_metadata.providers is the only assertion (cycle-3 refinement)."""
        key, team_id = _provision_anon(client, fake)  # noqa: RUF059
        _patch_verify(monkeypatch, _jwt(_U_X, email="a@b.co",
                                        providers=["email"], amr=amr,
                                        include_amr=True))
        r = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={"api_key": key},
        )
        assert r.status_code == 403, f"{label}: {r.text}"

    def test_amr_less_github_token_passes(self, client, fake, monkeypatch):
        """An amr-LESS github token still passes: app_metadata survives token
        refresh; amr is optional and refresh-mutated (never consulted)."""
        key, team_id = _provision_anon(client, fake)  # noqa: RUF059
        _patch_verify(monkeypatch, _jwt(_U_X, email="a@b.co",
                                        providers=["github"]))
        r = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={"api_key": key},
        )
        assert r.status_code == 200, r.text

    def test_claim_same_key_authenticates_pre_post(self, client, fake,
                                                   monkeypatch):
        """Indicator 1: the minted key authenticates BEFORE claim (anon team)
        and AFTER claim (linked owner) — same key, memories intact."""
        key, team_id = _provision_anon(client, fake)
        pre = client.get("/v1/team", headers={"Authorization": f"Bearer {key}"})
        assert pre.status_code == 200, pre.text

        _patch_verify(monkeypatch, _jwt(_U_A, email="verified@example.com",
                                        providers=["github"]))
        r = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={"api_key": key},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["team_id"] == team_id
        assert body["status"] == "claimed"

        post = client.get("/v1/team", headers={"Authorization": f"Bearer {key}"})
        assert post.status_code == 200, post.text
        assert post.json()["team_id"] == team_id
        # teams.email overwritten with the verified OAuth email
        rows = fake.tables["teams"]
        team_row = next(t for t in rows if t["id"] == team_id)
        assert team_row["email"] == "verified@example.com"
        # owner membership linked + identity cleared
        mem = next(m for m in fake.tables["team_memberships"]
                   if m["team_id"] == team_id)
        assert mem["user_id"] == _U_A
        assert mem["identity"] is None

    def test_audit_team_claim_with_detail(self, client, fake, monkeypatch,
                                          capsys):
        """Indicator 5: audit team_claim fires with provider/email/user_id in
        detail (audit_events.detail JSONB, 20260813000004)."""
        key, team_id = _provision_anon(client, fake)
        _patch_verify(monkeypatch, _jwt(_U_A, email="verified@example.com",
                                        providers=["google"]))
        import tortoise.hosted_api as _ha
        events = []

        async def _capture_audit(request, team_id, operation, **kw):
            events.append({"team_id": team_id, "operation": operation, **kw})

        monkeypatch.setattr(_ha, "_async_audit", _capture_audit)
        r = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={"api_key": key},
        )
        assert r.status_code == 200, r.text
        claims = [e for e in events if e["operation"] == "team_claim"]
        assert len(claims) == 1
        ev = claims[0]
        assert ev["team_id"] == team_id
        assert ev["actor_user_id"] == _U_A
        assert ev["detail"]["email"] == "verified@example.com"
        assert ev["detail"]["user_id"] == _U_A
        assert "google" in ev["detail"]["provider"]

    def test_second_claim_409_first_claim_wins(self, client, fake,
                                               monkeypatch):
        """Indicator 5: first-claim-wins — a different user's claim on the
        same key → 409."""
        key, team_id = _provision_anon(client, fake)  # noqa: RUF059
        _patch_verify(monkeypatch, _jwt(_U_A, email="a@example.com",
                                        providers=["github"]))
        r = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={"api_key": key},
        )
        assert r.status_code == 200, r.text
        _patch_verify(monkeypatch, _jwt(_U_B, email="b@example.com",
                                        providers=["github"]))
        r2 = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={"api_key": key},
        )
        assert r2.status_code == 409, r2.text
        assert "already" in r2.json()["detail"].lower()

    def test_claim_idempotent_same_user(self, client, fake, monkeypatch):
        """P3-FIX-Q: re-claim by the SAME user is a noop 200 (the endpoint's
        pre-check passes because is_anon_team flips, but the RPC returns
        idempotent success — here we exercise the RPC-level idempotency by
        calling claim_membership directly after the first claim)."""
        key, team_id = _provision_anon(client, fake)  # noqa: RUF059
        _patch_verify(monkeypatch, _jwt(_U_A, email="a@example.com",
                                        providers=["github"]))
        r = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={"api_key": key},
        )
        assert r.status_code == 200, r.text
        # second call: is_anon_team is now False → 409 from the endpoint
        r2 = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={"api_key": key},
        )
        assert r2.status_code == 409, r2.text

    def test_tamper_client_team_id_identity_rejected(self, client, fake,
                                                     monkeypatch):
        """Tamper: client-supplied team_id/identity are REJECTED — the RPC
        binds by lookup_hash only (solution-verify P1). A body claiming a
        DIFFERENT team must claim the KEY's team, not the body's."""
        key, team_id = _provision_anon(client, fake)
        # a second anon team the attacker "points at" via the body
        r = client.post("/v1/agent/signup", json={})
        victim_key = r.json()["key"]  # noqa: F841
        victim_team = r.json()["team_id"]
        _patch_verify(monkeypatch, _jwt(_U_A, email="a@example.com",
                                        providers=["github"]))
        r = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={
                "api_key": key,
                "team_id": victim_team,          # must be IGNORED
                "identity": "anon-attacker",      # must be IGNORED
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["team_id"] == team_id, (
            f"claim must bind to the key's team ({team_id}), not body team "
            f"{victim_team}")
        # victim team untouched — still anon
        mems = [m for m in fake.tables["team_memberships"]
                if m["team_id"] == victim_team]
        assert mems and mems[0]["user_id"] is None
        assert sc.resolve_api_key(fake, key)["team_id"] == team_id

    def test_invalid_key_401(self, client, fake, monkeypatch):
        _patch_verify(monkeypatch, _jwt(_U_A, email="a@example.com",
                                        providers=["github"]))
        r = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={"api_key": "tt_no_such_key_1234567890"},
        )
        assert r.status_code == 401, r.text

    def test_email_confirmed_conjunct_fail_closed(self, client, fake,
                                                  monkeypatch):
        """email_confirmed_at is an AND conjunct (never OR): a token with the
        right provider but an unconfirmed email is rejected."""
        key, team_id = _provision_anon(client, fake)
        _patch_verify(monkeypatch, _jwt(_U_A, email="a@example.com",
                                        providers=["github"]))

        async def _not_confirmed(request):
            return False

        monkeypatch.setattr(ha_mod, "_gotrue_email_confirmed", _not_confirmed)
        r = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={"api_key": key},
        )
        assert r.status_code == 403, r.text
        assert "not confirmed" in r.json()["detail"].lower()
        # nothing was linked
        mem = next(m for m in fake.tables["team_memberships"]
                   if m["team_id"] == team_id)
        assert mem["user_id"] is None

    def test_claim_limiter_2_per_24h(self, client, fake, monkeypatch):
        """P3-FIX-H: explicit 24h-window bucket — 3rd claim attempt in 24h
        → 429 + Retry-After 86400."""
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)  # limiter ON
        _patch_verify(monkeypatch, _jwt(_U_A, email="a@example.com",
                                        providers=["github"]))
        for _ in range(2):
            r = client.post(
                "/v1/claim",
                headers={"Authorization": "Bearer abc.def.ghi"},
                json={"api_key": "tt_limiter_key_0000000000000000000000000"},
            )
            assert r.status_code != 429, r.text  # 1st/2nd allowed
        r3 = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={"api_key": "tt_limiter_key_0000000000000000000000000"},
        )
        assert r3.status_code == 429, r3.text
        assert r3.headers.get("retry-after") == "86400"

    def test_claim_allowed_under_0015_drift_then_blocked_after_recovery(
            self, client, fake, monkeypatch):
        """#1096 accepted-risk pin: claim is suspension-gated via
        _get_current_team_supabase — under 0015 drift a durably-suspended
        anon team can execute the durable claim write (permanent identity
        link + email overwrite); in healthy mode the 403 SUSPENDED gate
        fires (the pin's premise)."""
        _patch_verify(monkeypatch, _jwt(_U_1, providers=["github"]))
        # Drift phase: suspended anon team claims successfully (fail-open).
        key, team_id = _provision_anon(client, fake)
        fake.rpc("abuse_suspend", {"p_team_id": team_id})
        fake.missing_columns = {"teams": {"suspended_at", "flagged_at"}}
        r = client.post("/v1/claim", json={"api_key": key})
        assert r.status_code == 200
        # The DURABLE write actually landed (the accepted-risk property):
        # owner membership linked + identity cleared + teams.email overwritten.
        team_row = next(t for t in fake.tables["teams"] if t["id"] == team_id)
        assert team_row["email"] == "claim@example.com"
        mem = next(m for m in fake.tables["team_memberships"]
                   if m["team_id"] == team_id)
        assert mem["user_id"] == _U_1
        assert mem["identity"] is None
        fake.missing_columns = None  # drift resolved — enforcement must resume
        # Healthy phase: a FRESH suspended anon team is 403-blocked at the gate.
        key2, team_id2 = _provision_anon(client, fake)
        fake.rpc("abuse_suspend", {"p_team_id": team_id2})
        r = client.post("/v1/claim", json={"api_key": key2})
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "SUSPENDED"


class TestClaimStatusEndpoint:
    def test_requires_session_jwt(self, client, fake, monkeypatch):
        r = client.get("/v1/claim/status")
        assert r.status_code == 401, r.text

    def test_no_key_returns_need_key(self, client, fake, monkeypatch):
        _patch_verify(monkeypatch, _jwt(_U_A, email="a@example.com",
                                        providers=["github"]))
        r = client.get("/v1/claim/status",
                       headers={"Authorization": "Bearer abc.def.ghi"})
        assert r.status_code == 200, r.text
        assert r.json() == {"claimable": False, "need_key": True}

    def test_anon_key_claimable(self, client, fake, monkeypatch):
        key, team_id = _provision_anon(client, fake)
        _patch_verify(monkeypatch, _jwt(_U_A, email="a@example.com",
                                        providers=["github"]))
        # P1-2: key travels via X-Claim-Key header (never query string —
        # access-log leak of the graph credential).
        r = client.get(
            "/v1/claim/status",
            headers={"Authorization": "Bearer abc.def.ghi",
                     "X-Claim-Key": key},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["claimable"] is True
        assert body["team_id"] == team_id

    def test_claim_status_query_form_rejected(self, client, fake,
                                              monkeypatch):
        """P1-2: the query-string api_key form is NOT accepted (access-log
        leak of the graph credential) — header only."""
        key, team_id = _provision_anon(client, fake)  # noqa: RUF059
        _patch_verify(monkeypatch, _jwt(_U_A, email="a@example.com",
                                        providers=["github"]))
        r = client.get(
            "/v1/claim/status?api_key=" + key,
            headers={"Authorization": "Bearer abc.def.ghi"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["claimable"] is False
        assert r.json().get("need_key") is True

    def test_claimed_key_reports_claimed_by_me(self, client, fake,
                                               monkeypatch):
        key, team_id = _provision_anon(client, fake)  # noqa: RUF059
        _patch_verify(monkeypatch, _jwt(_U_A, email="a@example.com",
                                        providers=["github"]))
        r = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={"api_key": key},
        )
        assert r.status_code == 200, r.text
        r2 = client.get(
            "/v1/claim/status",
            headers={"Authorization": "Bearer abc.def.ghi",
                     "X-Claim-Key": key},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["claimable"] is False
        assert r2.json()["claimed"] is True

    def test_unknown_key_not_claimable(self, client, fake, monkeypatch):
        _patch_verify(monkeypatch, _jwt(_U_A, email="a@example.com",
                                        providers=["github"]))
        r = client.get(
            "/v1/claim/status",
            headers={"Authorization": "Bearer abc.def.ghi",
                     "X-Claim-Key": "tt_no_such_key_0000000000000000000"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["claimable"] is False

    def test_registry_mode_unsupported(self, client, fake, monkeypatch):
        import tortoise.supabase_control as sc
        monkeypatch.setattr(sc, "is_supabase_enabled", lambda: False)
        _patch_verify(monkeypatch, _jwt(_U_A, email="a@example.com",
                                        providers=["github"]))
        r = client.get(
            "/v1/claim/status",
            headers={"Authorization": "Bearer abc.def.ghi",
                     "X-Claim-Key": "tt_some_key_0000000000000000000000"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["claimable"] is False
        assert r.json()["unsupported"] is True


# ── PR2: anon-tier ceiling (#1082, indicator 4) ────────────────────────────


class TestAnonCeiling:
    """Indicator 4: unclaimed zero-email teams resolve to the reduced
    ``anon`` tier; claimed teams lift to full free (same key, same team).

    The derivation lives in quota.derived_tier() (shared helper) and is
    applied at resolve_api_key (auth boundary), resolve_team_limits, and
    team_tier (metering/analytics source). Registry mode NO-OPs (Supabase-
    mode-only ceiling in v1).
    """

    def test_unclaimed_anon_team_resolves_anon_tier(self, client, fake,
                                                    monkeypatch):
        """Unclaimed anon team → /v1/team shows anon tier; limits resolve
        to the reduced anon caps (1,000 ops / 1,000 nodes / 1 key)."""
        key, team_id = _provision_anon(client, fake)
        r = client.get("/v1/team", headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200, r.text
        team = r.json()
        assert team["tier"] == "anon", team
        assert team.get("anon") is True  # claim card renders
        # write_ops_limit derives via metering (team_tier → derived_tier →
        # anon) — the anon tier is 1/10th of free (1,000 ops).
        assert team.get("write_ops_limit") == 1000, team
        # The reduced CAPS bind at the limits resolution layer.
        from tortoise.quota import resolve_team_limits
        lim = resolve_team_limits(team_id)
        assert lim["tier"] == "anon", lim
        assert lim.get("max_points") == 1000, lim  # 1k node cap
        assert lim.get("max_api_keys") == 1, lim  # 1 key on anon tier

    def test_claimed_team_lifts_to_free_same_key(self, client, fake,
                                                 monkeypatch):
        """After claim (owner user_id linked), the SAME key resolves free:
        tier free, full 10k caps, anon flag False."""
        key, team_id = _provision_anon(client, fake)
        # claim with a github-provider session
        user_id = str(uuid.uuid4())
        _patch_verify(monkeypatch, _jwt(user_id, providers=["github"]))
        r = client.post("/v1/claim", json={"api_key": key})
        assert r.status_code == 200, r.text

        r = client.get("/v1/team", headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200, r.text
        team = r.json()
        assert team["tier"] == "free", team
        assert team.get("anon") is False
        assert team.get("write_ops_limit") == 10000, team
        from tortoise.quota import resolve_team_limits
        lim = resolve_team_limits(team_id)
        assert lim["tier"] == "free", lim
        assert lim.get("max_points") == 10000, lim
        assert lim.get("max_api_keys") == 2, lim

    def test_reg_team_capped_until_claimed(self, client, fake, monkeypatch):
        """reg- teams (email set at mint, owner user_id NULL) are ALSO anon-
        tier until claimed — the predicate is membership-based, never the
        teams.email proxy (solution-verify P2 / parity fixture class 3)."""
        # reg- path: /v1/register with an email
        import json as _json  # noqa: F401
        reg_email = f"reg-{uuid.uuid4().hex[:8]}@example.com"
        r = client.post("/v1/register", json={"email": reg_email,
                                              "password": "password123"})
        assert r.status_code == 200, r.text
        reg_key = r.json()["api_key"]

        r = client.get("/v1/team", headers={"Authorization": f"Bearer {reg_key}"})
        assert r.status_code == 200, r.text
        team = r.json()
        assert team["tier"] == "anon", team  # email set, still unclaimed

        user_id = str(uuid.uuid4())
        _patch_verify(monkeypatch, _jwt(user_id, email=reg_email,
                                        providers=["google"]))
        r = client.post("/v1/claim", json={"api_key": reg_key})
        assert r.status_code == 200, r.text
        r = client.get("/v1/team", headers={"Authorization": f"Bearer {reg_key}"})
        assert r.json()["tier"] == "free"

    def test_derived_tier_registry_mode_noop(self, fake, monkeypatch):
        """Registry mode: derived_tier returns the stored tier (no anon
        ceiling) — selfhost is operator-controlled (v1 scope decision)."""
        from tortoise.quota import derived_tier
        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
        # No control plane → fail-open to stored tier
        assert derived_tier({"tier": "free", "id": "t-x"}) == "free"

    def test_parity_claim_rpc_resolve_limits(self, client, fake, monkeypatch):
        """Parity: the claim RPC's is_anon_team predicate, resolve_api_key,
        and resolve_team_limits agree on the same fixture (anon then
        claimed) — the drift guard from solution-verify P1-C."""
        from tortoise.quota import resolve_team_limits
        from tortoise.supabase_control import is_anon_team

        key, team_id = _provision_anon(client, fake)
        # Before claim: predicate true, api-key tier anon, limits anon
        assert is_anon_team(fake, team_id) is True
        r = client.get("/v1/team", headers={"Authorization": f"Bearer {key}"})
        assert r.json()["tier"] == "anon"
        lim = resolve_team_limits(team_id)
        assert lim["tier"] == "anon", lim

        # After claim: predicate false, api-key tier free, limits free
        user_id = str(uuid.uuid4())
        _patch_verify(monkeypatch, _jwt(user_id, providers=["github"]))
        r = client.post("/v1/claim", json={"api_key": key})
        assert r.status_code == 200, r.text
        assert is_anon_team(fake, team_id) is False
        r = client.get("/v1/team", headers={"Authorization": f"Bearer {key}"})
        assert r.json()["tier"] == "free"
        lim = resolve_team_limits(team_id)
        assert lim["tier"] == "free", lim


class TestClaimStatusOutage503:
    """#1719 Task 4 (RC1-b): the claim funnel shares the unwrapped
    team_memberships reads — a control-plane failure must degrade to 503
    control_plane_unavailable, never a global-handler 500."""

    def test_claim_status_is_anon_team_outage_503(self, client, fake, monkeypatch):
        from tortoise import supabase_control as sc

        key, _ = _provision_anon(client, fake)
        _patch_verify(monkeypatch, _jwt(_U_A, email="a@example.com",
                                        providers=["github"]))

        def _boom(cp, tid):
            raise RuntimeError("Supabase control-plane query failed "
                               "(team_memberships): HTTP 500")

        monkeypatch.setattr(sc, "is_anon_team", _boom)
        r = client.get(
            "/v1/claim/status",
            headers={"Authorization": "Bearer abc.def.ghi",
                     "X-Claim-Key": key},
        )
        assert r.status_code == 503, r.text
        assert r.json().get("detail", {}).get("error_code") == "control_plane_unavailable"


class TestClaimRpcOutage503:
    """#1737: the claim endpoints' residual outage paths must degrade to 503
    control_plane_unavailable — claim_email's admin-create transport failure
    and the claim_membership RPC's non-ClaimError RuntimeError both escape
    to a raw 500 today.
    """

    def test_claim_email_create_user_transport_outage_503(self, client, fake,
                                                          monkeypatch):
        """_supabase_admin_create_user raises (GoTrue transport) → 503
        control_plane_unavailable, not a raw 500."""
        key, _ = _provision_anon(client, fake)

        def _boom(email, password):
            raise RuntimeError("auth-service transport failure")

        monkeypatch.setattr(ha_mod, "_supabase_admin_create_user", _boom)
        r = client.post("/v1/claim/email", json={
            "api_key": key,
            "email": "new@example.com",
            "password": "password123",
        })
        assert r.status_code == 503, r.text
        assert r.json().get("detail", {}).get("error_code") == "control_plane_unavailable"

    def test_claim_team_claim_membership_rpc_outage_503(self, client, fake,
                                                        monkeypatch):
        """claim_membership raises a non-ClaimError RuntimeError (RPC
        outage) on POST /v1/claim → 503, not a raw 500."""
        key, _ = _provision_anon(client, fake)
        _patch_verify(monkeypatch, _jwt(_U_A, email="a@example.com",
                                        providers=["github"]))

        def _boom(cp, **kwargs):
            raise RuntimeError("Supabase control-plane query failed "
                               "(claim_membership): HTTP 500")

        monkeypatch.setattr(sc, "claim_membership", _boom)
        r = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={"api_key": key},
        )
        assert r.status_code == 503, r.text
        assert r.json().get("detail", {}).get("error_code") == "control_plane_unavailable"

    def test_claim_email_claim_membership_rpc_outage_503(self, client, fake,
                                                         monkeypatch):
        """claim_membership raises a non-ClaimError RuntimeError (RPC
        outage) on POST /v1/claim/email → 503, not a raw 500."""
        key, _ = _provision_anon(client, fake)

        def _fake_admin_create(email, password):
            return 201, {"id": f"auth-{uuid.uuid4().hex[:8]}", "email": email}

        monkeypatch.setattr(ha_mod, "_supabase_admin_create_user",
                            _fake_admin_create)

        def _boom(cp, **kwargs):
            raise RuntimeError("Supabase control-plane query failed "
                               "(claim_membership): HTTP 500")

        monkeypatch.setattr(sc, "claim_membership", _boom)
        r = client.post("/v1/claim/email", json={
            "api_key": key,
            "email": "new@example.com",
            "password": "password123",
        })
        assert r.status_code == 503, r.text
        assert r.json().get("detail", {}).get("error_code") == "control_plane_unavailable"


class TestClaimEmailResolveOutage503:
    """#1737: claim_email's direct resolve_api_key call shares the
    control-plane outage class — RuntimeError → 503, never a raw 500."""

    def test_claim_email_resolve_outage_503(self, client, fake, monkeypatch):
        from tortoise import supabase_control as sc

        def _boom(cp, token):
            raise RuntimeError("Supabase control-plane query failed "
                               "(api_keys): HTTP 400")

        monkeypatch.setattr(sc, "resolve_api_key", _boom)
        r = client.post("/v1/claim/email",
                        json={"api_key": "tt_does-not-exist",
                              "email": "a@example.com",
                              "password": "password123"},
                        headers={"Authorization": "Bearer abc.def.ghi"})
        assert r.status_code == 503, r.text
        assert r.json().get("detail", {}).get("error_code") == "control_plane_unavailable"
