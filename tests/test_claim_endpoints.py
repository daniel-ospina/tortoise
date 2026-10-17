"""POST /v1/claim + GET /v1/claim/status endpoint tests (#1082, PR1).

Claim = ONE endpoint requiring BOTH credentials: a fresh Supabase session
JWT (Authorization header, verified server-side) + the pasted tt_ key
(body.api_key — key-possession anchor). The provider-verified-email
invariant (app_metadata.providers ∩ {github,google} ≠ ∅) + the
email_confirmed_at conjunct gate the claim BEFORE the RPC.

Covered here (all Supabase-mode via the FakeControlPlane):
- bad/missing JWT → 401
- null email → fail-closed 400
- #1765 demotion: email+password-only sessions MAY claim (the provider gate
  is lifted; claim never writes teams.email — confirmed-email conjunct + key-
  possession remain)
- provider-invariant negatives: fresh password token / refreshed password
  token (amr='token_refresh', no github/google provider) → 403; an
  amr-LESS github token still passes (app_metadata survives refresh —
  amr is NEVER the invariant source)
- github/google provider → 200; same key still resolves pre/post
  (indicator 1); audit team_claim with detail
- second claim by a different user → 409 first-claim-wins
- tamper: client-supplied org_id/identity REJECTED — the RPC binds by
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
from tests._verdict import verdict_async_client, verdict_client
from tests.fake_control_plane import FakeControlPlane

_SUPABASE_URL = "https://claimtest.supabase.co"

# #1719 (Task 3): JWT subjects are real UUIDs — claim_status's
# membership_for_user_org filters org_memberships.user_id (uuid column);
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
    ha_mod._CLAIM_OVERFLOW.clear()
    fake = FakeControlPlane()
    monkeypatch.setattr(sc, "get_control_plane", lambda: fake)

    async def _confirmed(request):
        return True

    monkeypatch.setattr(ha_mod, "_gotrue_email_confirmed", _confirmed)
    yield fake
    ha_mod._CLAIM_BUCKETS.clear()
    ha_mod._CLAIM_OVERFLOW.clear()


@pytest.fixture

def _fake_from_env(_claim_env):
    """Expose the _claim_env-created FakeControlPlane as `fake`."""
    return _claim_env


@pytest.fixture
def fake(_fake_from_env):
    return _fake_from_env


@pytest.fixture
def client():
    with verdict_client(app) as c:
        yield c


def _provision_anon(client, fake, *, org_id=None, identity=None, email=None):
    """Mint an anonymous team through the real /v1/agent/signup path.

    Returns the plaintext key. The signup writes through the fake's
    provision_team RPC (Supabase mode) — the same seam the endpoint uses.
    """
    if org_id is None:
        org_id = f"team-{uuid.uuid4().hex[:10]}"
    if identity is None:
        identity = f"anon-{uuid.uuid4().hex[:12]}"
    r = client.post("/v1/agent/signup", json={})
    assert r.status_code == 200, r.text
    data = r.json()
    return data["key"], data["org_id"]


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

    def test_password_only_session_can_claim(self, client, fake, monkeypatch):
        """#1765 demotion: the providers ∩ {github,google} gate is LIFTED —
        claim no longer writes teams.email, so a confirmed email+password
        session may claim (key-possession + confirmed-email + first-claim-
        wins remain the security model)."""
        key, org_id = _provision_anon(client, fake)  # noqa: RUF059
        _patch_verify(monkeypatch, _jwt(_U_X, providers=["email"],
                                        email="pw@example.com"))

        r = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={"api_key": key},
        )
        assert r.status_code == 200, r.text

    def test_password_token_on_linked_account_passes(self, client, fake,
                                                     monkeypatch):
        """A password login on a github-LINKED account legitimately passes:
        providers accumulates on linking (app_metadata survives refresh).
        This is the intended semantics (documented in the plan)."""
        key, org_id = _provision_anon(client, fake)  # noqa: RUF059
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
    def test_password_token_amr_never_used(self, client, fake,
                                           monkeypatch, amr, label):
        """#1765 demotion: a PASSWORD session may claim (the provider gate is
        lifted; amr remains irrelevant — app_metadata.providers is not the
        gate anymore either). Kept parametrized to pin that amr presence or
        absence never changes the outcome."""
        key, org_id = _provision_anon(client, fake)  # noqa: RUF059
        _patch_verify(monkeypatch, _jwt(_U_X, email="a@b.co",
                                        providers=["email"], amr=amr,
                                        include_amr=True))
        r = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={"api_key": key},
        )
        assert r.status_code == 200, f"{label}: {r.text}"

    def test_amr_less_github_token_passes(self, client, fake, monkeypatch):
        """An amr-LESS github token still passes: app_metadata survives token
        refresh; amr is optional and refresh-mutated (never consulted)."""
        key, org_id = _provision_anon(client, fake)  # noqa: RUF059
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
        key, org_id = _provision_anon(client, fake)
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
        assert body["org_id"] == org_id
        assert body["status"] == "claimed"

        post = client.get("/v1/team", headers={"Authorization": f"Bearer {key}"})
        assert post.status_code == 200, post.text
        assert post.json()["org_id"] == org_id
        # #1765 demotion: claim never writes teams.email
        rows = fake.tables["organizations"]
        org_row = next(t for t in rows if t["id"] == org_id)
        assert org_row.get("email") is None
        # owner membership linked + identity cleared
        mem = next(m for m in fake.tables["org_memberships"]
                   if m["org_id"] == org_id)
        assert mem["user_id"] == _U_A
        assert mem["identity"] is None

    def test_audit_team_claim_with_detail(self, client, fake, monkeypatch,
                                          capsys):
        """Indicator 5: audit team_claim fires with provider/email/user_id in
        detail (audit_events.detail JSONB, 20260813000004)."""
        key, org_id = _provision_anon(client, fake)
        _patch_verify(monkeypatch, _jwt(_U_A, email="verified@example.com",
                                        providers=["google"]))
        import tortoise.hosted_api as _ha
        events = []

        async def _capture_audit(request, org_id, operation, **kw):
            events.append({"org_id": org_id, "operation": operation, **kw})

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
        assert ev["org_id"] == org_id
        assert ev["actor_user_id"] == _U_A
        assert ev["detail"]["email"] == "verified@example.com"
        assert ev["detail"]["user_id"] == _U_A
        assert "google" in ev["detail"]["provider"]

    def test_second_claim_409_first_claim_wins(self, client, fake,
                                               monkeypatch):
        """Indicator 5: first-claim-wins — a different user's claim on the
        same key → 409."""
        key, org_id = _provision_anon(client, fake)  # noqa: RUF059
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
        pre-check passes because is_anon_org flips, but the RPC returns
        idempotent success — here we exercise the RPC-level idempotency by
        calling claim_membership directly after the first claim)."""
        key, org_id = _provision_anon(client, fake)  # noqa: RUF059
        _patch_verify(monkeypatch, _jwt(_U_A, email="a@example.com",
                                        providers=["github"]))
        r = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={"api_key": key},
        )
        assert r.status_code == 200, r.text
        # second call: is_anon_org is now False → 409 from the endpoint
        r2 = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={"api_key": key},
        )
        assert r2.status_code == 409, r2.text

    def test_tamper_client_org_id_identity_rejected(self, client, fake,
                                                     monkeypatch):
        """Tamper: client-supplied org_id/identity are REJECTED — the RPC
        binds by lookup_hash only (solution-verify P1). A body claiming a
        DIFFERENT team must claim the KEY's team, not the body's."""
        key, org_id = _provision_anon(client, fake)
        # a second anon team the attacker "points at" via the body
        r = client.post("/v1/agent/signup", json={})
        victim_key = r.json()["key"]  # noqa: F841
        victim_team = r.json()["org_id"]
        _patch_verify(monkeypatch, _jwt(_U_A, email="a@example.com",
                                        providers=["github"]))
        r = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={
                "api_key": key,
                "org_id": victim_team,          # must be IGNORED
                "identity": "anon-attacker",      # must be IGNORED
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["org_id"] == org_id, (
            f"claim must bind to the key's team ({org_id}), not body team "
            f"{victim_team}")
        # victim team untouched — still anon
        mems = [m for m in fake.tables["org_memberships"]
                if m["org_id"] == victim_team]
        assert mems and mems[0]["user_id"] is None
        assert sc.resolve_api_key(fake, key)["org_id"] == org_id

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
        key, org_id = _provision_anon(client, fake)
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
        mem = next(m for m in fake.tables["org_memberships"]
                   if m["org_id"] == org_id)
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

    def test_claim_limiter_is_per_real_client_ip(self, client, fake,
                                                 monkeypatch):
        """#3125: two clients behind the Fly proxy from DIFFERENT source IPs
        must NOT share a claim budget.

        Pre-fix the limiter keyed on ``request.client.host``, which behind
        Fly is the PROXY's IP — a constant — so both clients shared ONE
        bucket and the bundle was a GLOBAL 2/24h cap: the second client's
        FIRST attempt was already the global 3rd and 429'd. The requests are
        INTERLEAVED so the assertion cannot pass on sequential budgets alone.
        ``ClientIPMiddleware`` resolves ``request.state.client_ip`` from
        ``Fly-Client-IP`` when ``TORTOISE_TRUST_FLY_CLIENT_IP=1`` (#1559).
        """
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)  # ON
        monkeypatch.setenv("TORTOISE_TRUST_FLY_CLIENT_IP", "1")
        monkeypatch.setenv("TORTOISE_CLAIM_MAX_PER_24H", "2")
        monkeypatch.setenv("TORTOISE_CLAIM_WINDOW_S", "86400")
        _patch_verify(monkeypatch, _jwt(_U_A, email="a@example.com",
                                        providers=["github"]))

        def attempt(fly_ip):
            return client.post(
                "/v1/claim",
                headers={"Authorization": "Bearer abc.def.ghi",
                         "Fly-Client-IP": fly_ip},
                json={"api_key": "tt_limiter_key_0000000000000000000000000"},
            )

        a, b = "203.0.113.7", "198.51.100.9"
        # 2 attempts each, interleaved: every one is within ITS OWN budget.
        for ip in (a, b, a, b):
            r = attempt(ip)
            assert r.status_code != 429, (ip, r.text)
        # each client's 3rd attempt exhausts its own (not a shared) budget
        assert attempt(a).status_code == 429
        assert attempt(b).status_code == 429
        # the real assertion: TWO distinct buckets, keyed on the real client
        # IP — not one bucket keyed on the constant proxy host.
        assert set(ha_mod._CLAIM_BUCKETS) == {a, b}

        # The IPv4-mapped spelling of an ALREADY-SEEN address is the same
        # client, so it shares that budget instead of minting a second bucket
        # (a dual-stack client would otherwise get 2x the budget). The other
        # per-IP limiters get this inside _check_ip_bucket_rate_limit.
        assert attempt(f"::ffff:{b}").status_code == 429
        assert set(ha_mod._CLAIM_BUCKETS) == {a, b}

    def test_claim_limiter_store_is_bounded_and_overflow_fails_closed(
            self, client, fake, monkeypatch):
        """#3125: the store is BOUNDED — never the unbounded distinct-new-key
        growth the pre-fix prune allowed.

        That branch pruned only *stale* buckets, AFTER the charge (#2866's
        non-bounding shape): under a fresh-key flood every bucket is
        in-window, so nothing was reclaimed and the store grew without bound.
        Now: (1) inactive LRU-head buckets are reclaimed on the new-key path;
        (2) while the store is full of LIVE buckets a new IP gets NO bucket of
        its own — it shares ONE overflow bucket with the same 2/24h budget
        (fail closed: a bounded 429, never an unbounded budget); (3) a
        tracked key's budget is never reset by other keys' churn; (4) the
        store never exceeds ``TORTOISE_CLAIM_STORE_CAP``.
        """
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)  # ON
        monkeypatch.setenv("TORTOISE_TRUST_FLY_CLIENT_IP", "1")
        monkeypatch.setenv("TORTOISE_CLAIM_STORE_CAP", "2")
        monkeypatch.setenv("TORTOISE_CLAIM_MAX_PER_24H", "2")
        monkeypatch.setenv("TORTOISE_CLAIM_WINDOW_S", "86400")
        _patch_verify(monkeypatch, _jwt(_U_A, email="a@example.com",
                                        providers=["github"]))
        # A frozen clock drives the aging leg (the #2866 test pattern).
        clock = [1_000_000.0]
        monkeypatch.setattr(ha_mod.time, "time", lambda: clock[0])

        def overflow():
            return len(ha_mod._CLAIM_OVERFLOW[ha_mod._CLAIM_OVERFLOW_KEY])

        def attempt(fly_ip):
            return client.post(
                "/v1/claim",
                headers={"Authorization": "Bearer abc.def.ghi",
                         "Fly-Client-IP": fly_ip},
                json={"api_key": "tt_limiter_key_0000000000000000000000000"},
            )

        # two distinct IPs fill the store TO its cap
        for ip in ("203.0.113.1", "203.0.113.2"):
            assert attempt(ip).status_code != 429
        assert len(ha_mod._CLAIM_BUCKETS) == 2

        # a THIRD new IP while both buckets are LIVE: no bucket of its own,
        # charged to the shared overflow bucket — the store does NOT grow.
        assert attempt("203.0.113.3").status_code != 429
        assert len(ha_mod._CLAIM_BUCKETS) == 2
        assert "203.0.113.3" not in ha_mod._CLAIM_BUCKETS
        assert overflow() == 1

        # overflow binds at the SAME budget (2/24h) — fail-closed, not
        # unlimited: the 2nd charges, the 3rd is 429, store still bounded.
        assert attempt("203.0.113.4").status_code != 429
        assert overflow() == 2
        r = attempt("203.0.113.5")
        assert r.status_code == 429, r.text
        assert len(ha_mod._CLAIM_BUCKETS) == 2

        # a TRACKED key's budget is not reset by the other keys' churn:
        # 203.0.113.1 already spent 1 — its 2nd is allowed, its 3rd 429s.
        assert attempt("203.0.113.1").status_code != 429
        assert attempt("203.0.113.1").status_code == 429

        # (1) aging: every stored bucket is now inactive, so a new IP RECLAIMS
        # them and gets its own bucket — a full store never permanently
        # denies new clients, and the store stays within the cap.
        clock[0] += 24 * 3600 + 1
        assert attempt("203.0.113.9").status_code != 429
        assert "203.0.113.9" in ha_mod._CLAIM_BUCKETS
        assert len(ha_mod._CLAIM_BUCKETS) <= 2

    def test_claim_limiter_invalid_window_never_fails_open(
            self, client, fake, monkeypatch):
        """#3125: a NON-POSITIVE ``TORTOISE_CLAIM_WINDOW_S`` must not
        silently disable the limiter.

        With ``window_s <= 0`` the in-window test ``now - t < window_s`` is
        never true, so the bucket is emptied on every request and EVERY claim
        is allowed — a fail-OPEN, and the knob is new here (#3125 item 3).
        An invalid window falls back to the default instead; the intended
        off-switch is RATE_LIMIT_DISABLED. The advertised Retry-After proves
        the fallback (it is the default window, not 0).
        """
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)  # ON
        monkeypatch.setenv("TORTOISE_CLAIM_WINDOW_S", "0")
        monkeypatch.setenv("TORTOISE_CLAIM_MAX_PER_24H", "2")
        monkeypatch.setenv("TORTOISE_CLAIM_STORE_CAP", "10000")
        _patch_verify(monkeypatch, _jwt(_U_A, email="a@example.com",
                                        providers=["github"]))

        def attempt():
            return client.post(
                "/v1/claim",
                headers={"Authorization": "Bearer abc.def.ghi"},
                json={"api_key": "tt_limiter_key_0000000000000000000000000"},
            )

        for _ in range(2):
            assert attempt().status_code != 429
        r = attempt()
        assert r.status_code == 429, r.text
        assert r.headers.get("retry-after") == "86400"

    def test_claim_allowed_under_0015_drift_then_blocked_after_recovery(
            self, client, fake, monkeypatch):
        """#1096 accepted-risk pin: claim is suspension-gated via
        _get_current_team_supabase — under 0015 drift a durably-suspended
        anon team can execute the durable claim write (permanent identity
        link + email overwrite); in healthy mode the 403 SUSPENDED gate
        fires (the pin's premise)."""
        _patch_verify(monkeypatch, _jwt(_U_1, providers=["github"]))
        # Drift phase: suspended anon team claims successfully (fail-open).
        key, org_id = _provision_anon(client, fake)
        fake.rpc("abuse_suspend", {"p_org_id": org_id})
        fake.missing_columns = {"organizations": {"suspended_at", "flagged_at"}}
        r = client.post("/v1/claim", json={"api_key": key})
        assert r.status_code == 200
        # The DURABLE write actually landed (the accepted-risk property):
        # owner membership linked + identity cleared. #1765: teams.email
        # is NEVER written by claim.
        org_row = next(t for t in fake.tables["organizations"] if t["id"] == org_id)
        assert org_row.get("email") is None
        mem = next(m for m in fake.tables["org_memberships"]
                   if m["org_id"] == org_id)
        assert mem["user_id"] == _U_1
        assert mem["identity"] is None
        fake.missing_columns = None  # drift resolved — enforcement must resume
        # Healthy phase: a FRESH suspended anon team is 403-blocked at the gate.
        key2, org_id2 = _provision_anon(client, fake)
        fake.rpc("abuse_suspend", {"p_org_id": org_id2})
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
        key, org_id = _provision_anon(client, fake)
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
        assert body["org_id"] == org_id

    def test_claim_status_query_form_rejected(self, client, fake,
                                              monkeypatch):
        """P1-2: the query-string api_key form is NOT accepted (access-log
        leak of the graph credential) — header only."""
        key, org_id = _provision_anon(client, fake)  # noqa: RUF059
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
        key, org_id = _provision_anon(client, fake)  # noqa: RUF059
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
    applied at resolve_api_key (auth boundary), resolve_org_limits, and
    team_tier (metering/analytics source). Registry mode NO-OPs (Supabase-
    mode-only ceiling in v1).
    """

    def test_unclaimed_anon_team_resolves_anon_tier(self, client, fake,
                                                    monkeypatch):
        """Unclaimed anon team → /v1/team shows anon tier; limits resolve
        to the reduced anon caps (1,000 ops / 1,000 nodes / 1 key)."""
        key, org_id = _provision_anon(client, fake)
        r = client.get("/v1/team", headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200, r.text
        team = r.json()
        assert team["tier"] == "anon", team
        assert team.get("anon") is True  # claim card renders
        # write_ops_limit derives via metering (team_tier → derived_tier →
        # anon) — the anon tier is 1/10th of free (1,000 ops).
        assert team.get("write_ops_limit") == 1000, team
        # The reduced CAPS bind at the limits resolution layer.
        from tortoise.quota import resolve_org_limits
        lim = resolve_org_limits(org_id)
        assert lim["tier"] == "anon", lim
        assert lim.get("max_points") == 1000, lim  # 1k node cap
        assert lim.get("max_api_keys") == 1, lim  # 1 key on anon tier

    def test_claimed_team_lifts_to_free_same_key(self, client, fake,
                                                 monkeypatch):
        """After claim (owner user_id linked), the SAME key resolves free:
        tier free, full 10k caps, anon flag False."""
        key, org_id = _provision_anon(client, fake)
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
        from tortoise.quota import resolve_org_limits
        lim = resolve_org_limits(org_id)
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

    def test_max_points_override_honored_by_resolve_team_limits(
            self, client, fake, monkeypatch):
        """#1859 P3-2: a teams.max_points override (migration
        20260817000001) binds at resolve_org_limits (supabase lane) with
        graph_size_cap fallback when NULL — mirroring import_team's
        precedence; anon ceiling still wins (checked in
        test_unclaimed_anon_team_resolves_anon_tier)."""
        from tortoise.quota import resolve_org_limits
        key, org_id = _provision_anon(client, fake)
        user_id = str(uuid.uuid4())
        _patch_verify(monkeypatch, _jwt(user_id, providers=["github"]))
        r = client.post("/v1/claim", json={"api_key": key})
        assert r.status_code == 200, r.text
        # stored caps (free values) read-time override → 12345 wins
        fake.tables["organizations"][0]["max_points"] = 12345
        lim = resolve_org_limits(org_id)
        assert lim["max_points"] == 12345, lim
        # NULL override → graph_size_cap fallback (GAP-B)
        fake.tables["organizations"][0]["max_points"] = None
        lim = resolve_org_limits(org_id)
        assert lim["max_points"] == fake.tables["organizations"][0]["graph_size_cap"], lim

    def test_parity_claim_rpc_resolve_limits(self, client, fake, monkeypatch):
        """Parity: the claim RPC's is_anon_org predicate, resolve_api_key,
        and resolve_org_limits agree on the same fixture (anon then
        claimed) — the drift guard from solution-verify P1-C."""
        from tortoise.quota import resolve_org_limits
        from tortoise.supabase_control import is_anon_org

        key, org_id = _provision_anon(client, fake)
        # Before claim: predicate true, api-key tier anon, limits anon
        assert is_anon_org(fake, org_id) is True
        r = client.get("/v1/team", headers={"Authorization": f"Bearer {key}"})
        assert r.json()["tier"] == "anon"
        lim = resolve_org_limits(org_id)
        assert lim["tier"] == "anon", lim

        # After claim: predicate false, api-key tier free, limits free
        user_id = str(uuid.uuid4())
        _patch_verify(monkeypatch, _jwt(user_id, providers=["github"]))
        r = client.post("/v1/claim", json={"api_key": key})
        assert r.status_code == 200, r.text
        assert is_anon_org(fake, org_id) is False
        r = client.get("/v1/team", headers={"Authorization": f"Bearer {key}"})
        assert r.json()["tier"] == "free"
        lim = resolve_org_limits(org_id)
        assert lim["tier"] == "free", lim


class TestClaimStatusOutage503:
    """#1719 Task 4 (RC1-b): the claim funnel shares the unwrapped
    org_memberships reads — a control-plane failure must degrade to 503
    control_plane_unavailable, never a global-handler 500."""

    def test_claim_status_is_anon_team_outage_503(self, client, fake, monkeypatch):
        from tortoise import supabase_control as sc

        key, _ = _provision_anon(client, fake)
        _patch_verify(monkeypatch, _jwt(_U_A, email="a@example.com",
                                        providers=["github"]))

        def _boom(cp, tid):
            raise RuntimeError("Supabase control-plane query failed "
                               "(org_memberships): HTTP 500")

        monkeypatch.setattr(sc, "is_anon_org", _boom)
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


# ── #2032 body-sweep cap ────────────────────────────────────────────────────


def _oversized_chunked(n_chunks: int = 8, step: int = 8192):
    """Chunked generator, NO content-length — forces the streaming-cap path."""
    for _ in range(n_chunks):
        yield b"x" * step


class TestClaimBodySweepCap:
    def test_claim_oversized_413(self, client, monkeypatch):
        """claim_team — after the JWT + confirmed-email gates, an oversized
        chunked body → 413 (not remapped into the {} coerce)."""
        monkeypatch.setattr(ha_mod, "_BODY_MAX_BYTES", 256)
        _patch_verify(monkeypatch, _jwt(_U_X, email="cap@example.com",
                                        providers=["email"]))
        r = client.post(
            "/v1/claim", content=_oversized_chunked(),
            headers={"Authorization": "Bearer abc.def.ghi",
                     "content-type": "application/json"})
        assert r.status_code == 413
        assert r.json()["detail"] == ha_mod._BODY_413_DETAIL

    def test_claim_malformed_coerced_400_preserved(self, client, monkeypatch):
        """Malformed JSON → {} coerce → missing api_key → 400 (unchanged)."""
        _patch_verify(monkeypatch, _jwt(_U_X, email="cap@example.com",
                                        providers=["email"]))
        r = client.post(
            "/v1/claim", content=b"{oops",
            headers={"Authorization": "Bearer abc.def.ghi",
                     "content-type": "application/json"})
        assert r.status_code == 400
        assert "api_key" in r.json()["detail"]


# ── #2810: the claim lane consults the ONE free-org entitlement chokepoint ──


def _seed_owned_free_org(fake, org_id: str, user_id: str,
                         *, tier: str = "free",
                         status: str | None = None,
                         role: str = "owner") -> None:
    """Seed an org + an active membership row for *user_id*."""
    from tests.test_supabase_control import FREE_TEAM, _membership_row
    fake.seed("organizations", [dict(FREE_TEAM, id=org_id, name=org_id,
                                     tier=tier, subscription_status=status)])
    fake.seed("org_memberships", [_membership_row(user_id=user_id,
                                                   org_id=org_id, role=role)])


def _is_still_anon(fake, org_id: str) -> bool:
    return any(
        str(r.get("org_id")) == org_id and r.get("role") == "owner"
        and r.get("status") == "active" and r.get("user_id") is None
        for r in fake.tables.get("org_memberships", []))


class TestClaimFreeOrgEntitlement:
    """#2810: `/v1/claim` binds an anon org to the caller as its OWNER, so it
    must consult the same ownership-based entitlement every create-org lane
    consults — otherwise one person can end up owning N free orgs by minting N
    anon orgs (`/v1/agent/signup`) and claiming each in turn."""

    def test_capped_owner_cannot_claim_a_second_free_org(self, client, fake,
                                                         monkeypatch):
        """The hole: an owner of one free org claims an anon org → 402, the
        anon org is left unclaimed and the owned set is unchanged."""
        _seed_owned_free_org(fake, "team-free-a", _U_A)
        key, anon_org = _provision_anon(client, fake)
        _patch_verify(monkeypatch, _jwt(_U_A, email="a@example.com",
                                        providers=["github"]))
        r = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={"api_key": key},
        )
        assert r.status_code == 402, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "one_free_org_limit"
        assert detail["org_id"] == "team-free-a"
        # Non-consuming: the anon org is still claimable by a first-time user,
        # and no second owner row was written for the capped user.
        assert _is_still_anon(fake, anon_org)
        assert not any(
            str(row.get("org_id")) == anon_org and row.get("user_id") == _U_A
            for row in fake.tables.get("org_memberships", []))

    def test_claim_twice_by_one_user_yields_one_owned_free_org(self, client,
                                                               fake,
                                                               monkeypatch):
        """The cross-lane invariant on the claim lane: N anon orgs minted,
        all claimed by ONE user → at most one owned free org."""
        _patch_verify(monkeypatch, _jwt(_U_A, email="a@example.com",
                                        providers=["github"]))
        first_key, first_org = _provision_anon(client, fake)
        r1 = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={"api_key": first_key},
        )
        assert r1.status_code == 200, r1.text
        second_key, _second_org = _provision_anon(client, fake)
        r2 = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={"api_key": second_key},
        )
        assert r2.status_code == 402, r2.text
        assert r2.json()["detail"]["org_id"] == first_org
        from tortoise.supabase_control import owned_free_org_ids
        assert owned_free_org_ids(fake, _U_A) == [first_org]

    def test_collaborator_on_a_free_org_can_still_claim(self, client, fake,
                                                        monkeypatch):
        """The #2789 motivating regression, now pinned on the claim lane: a
        mere COLLABORATOR owns nothing, so the claim still succeeds."""
        _seed_owned_free_org(fake, "team-free-a", _U_A, role="member")
        key, _ = _provision_anon(client, fake)
        _patch_verify(monkeypatch, _jwt(_U_A, email="a@example.com",
                                        providers=["github"]))
        r = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={"api_key": key},
        )
        assert r.status_code == 200, r.text

    def test_paid_owner_can_still_claim(self, client, fake, monkeypatch):
        """Rules table: an owned org WITH an active paid subscription is not a
        free org, so it does not consume the allowance."""
        _seed_owned_free_org(fake, "team-paid-a", _U_A, tier="pro",
                             status="active")
        key, _ = _provision_anon(client, fake)
        _patch_verify(monkeypatch, _jwt(_U_A, email="a@example.com",
                                        providers=["github"]))
        r = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={"api_key": key},
        )
        assert r.status_code == 200, r.text

    def test_already_claimed_409_precedes_the_402(self, client, fake,
                                                  monkeypatch):
        """Ordering pin (parity with the create lanes' 429 → 409 → 402): a
        capped user presenting a key for an org someone else already claimed
        gets 409, not 402."""
        _seed_owned_free_org(fake, "team-free-a", _U_A)
        key, anon_org = _provision_anon(client, fake)
        # Another user claimed it first.
        _seed_owned_free_org(fake, anon_org, _U_B)
        # ...and the anon anchor is gone.
        fake.tables["org_memberships"] = [
            row for row in fake.tables["org_memberships"]
            if not (str(row.get("org_id")) == anon_org
                    and row.get("user_id") is None)]
        _patch_verify(monkeypatch, _jwt(_U_A, email="a@example.com",
                                        providers=["github"]))
        r = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={"api_key": key},
        )
        assert r.status_code == 409, r.text

    def test_claim_email_refuses_a_pre_existing_owner(self, client, fake,
                                                      monkeypatch):
        """`/v1/claim/email` is invariant-preserving because it creates a
        BRAND-NEW auth user (an existing email is a 409 at the admin-create
        step), and a brand-new user owns nothing. That safety rests on an
        external API's behaviour, so the chokepoint is applied here too: if
        admin-create ever returns an EXISTING user, the claim must be refused.
        """
        _seed_owned_free_org(fake, "team-free-a", _U_A)
        key, anon_org = _provision_anon(client, fake)

        def _existing_user(email, password):
            return 200, {"id": _U_A, "email": email}

        monkeypatch.setattr(ha_mod, "_supabase_admin_create_user",
                            _existing_user)
        r = client.post("/v1/claim/email", json={
            "api_key": key,
            "email": "new@example.com",
            "password": "password123",
        })
        assert r.status_code == 402, r.text
        assert r.json()["detail"]["code"] == "one_free_org_limit"
        assert _is_still_anon(fake, anon_org)
        assert "claim_membership" not in [fn for fn, _ in fake.rpc_calls]

    def test_claim_email_fresh_user_still_succeeds(self, client, fake,
                                                   monkeypatch):
        """The normal path is untouched: a freshly created user claims and
        owns exactly one free org."""
        key, anon_org = _provision_anon(client, fake)

        def _fresh_user(email, password):
            return 201, {"id": _U_A, "email": email}

        monkeypatch.setattr(ha_mod, "_supabase_admin_create_user",
                            _fresh_user)
        r = client.post("/v1/claim/email", json={
            "api_key": key,
            "email": "new@example.com",
            "password": "password123",
        })
        assert r.status_code == 200, r.text
        from tortoise.supabase_control import owned_free_org_ids
        assert owned_free_org_ids(fake, _U_A) == [anon_org]

    def test_concurrent_claims_by_one_user_yield_one_free_org(
            self, client, fake, monkeypatch):
        """The gate read (`_owned_free_org_ids`) and the `claim_membership`
        write are read-then-write (#1954), so the claim lane holds the same
        per-user `_org_create_lock` the create lanes hold. Without it two
        concurrent claims of two DIFFERENT anon orgs could each read "owns
        nothing" and both commit — re-opening the hole through concurrency.
        Verified as a REPRODUCER, not merely an outcome guard: with the
        `_org_create_lock` removed from the claim lane this test fails (both
        claims 200, two owned orgs). Mutation-verified 2026-10-13."""
        _patch_verify(monkeypatch, _jwt(_U_A, email="a@example.com",
                                        providers=["github"]))
        key_a, _org_a = _provision_anon(client, fake)
        key_b, _org_b = _provision_anon(client, fake)
        import asyncio

        from tortoise.hosted_api import app as _app

        async def _burst():
            async with verdict_async_client(_app,
                                            base_url="http://test") as ac:
                return await asyncio.gather(
                    ac.post("/v1/claim",
                            headers={"Authorization": "Bearer abc.def.ghi"},
                            json={"api_key": key_a}),
                    ac.post("/v1/claim",
                            headers={"Authorization": "Bearer abc.def.ghi"},
                            json={"api_key": key_b}),
                )

        r1, r2 = asyncio.run(_burst())
        assert sorted((r1.status_code, r2.status_code)) == [200, 402], (
            r1.text, r2.text)
        denied = r1 if r1.status_code == 402 else r2
        assert denied.json()["detail"]["code"] == "one_free_org_limit"
        from tortoise.supabase_control import owned_free_org_ids
        owned = owned_free_org_ids(fake, _U_A)
        assert len(owned) == 1, owned


def test_the_client_seam_turns_a_wait_bound_refusal_into_an_inconclusive_skip(
        monkeypatch):
    """#8802: the deterministic refusal pin for this module's client seams.

    No load and no wall-clock wait: the bound is forced to 0 s, so the refusal
    comes from the REAL ``WaitBoundMiddleware``. ``POST /v1/claim`` is the route
    this file reads most and it is not exempt from the bound. The plain-client
    control is the non-vacuity leg — it is the exact ``assert 504 == 200`` shape
    this conversion removes, and it would pass unchanged if the route were ever
    exempted.
    """
    from tortoise import mcp_auth
    from tortoise.hosted_api import _TRANSPORT_WAIT_BOUND_EXEMPT

    route = ("POST", "/v1/claim")
    assert route not in _TRANSPORT_WAIT_BOUND_EXEMPT
    monkeypatch.setattr(mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 0.0)
    with TestClient(app) as raw:
        assert raw.post("/v1/claim", json={}).status_code == 504
    with verdict_client(app) as tc, pytest.raises(pytest.skip.Exception) as excinfo:
        tc.post("/v1/claim", json={})
    message = str(excinfo.value)
    assert "INCONCLUSIVE [#5049]" in message
    assert "POST /v1/claim answered" in message
