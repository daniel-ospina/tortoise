"""POST /v1/user/identity/{link-intent,link-commit,unlink,resend-confirmation}
endpoint tests (#1765, plan Task 3). Server-authority gates: fail-closed
linking, HMAC intent refs, consumed-once, re-auth freshness, permit floor,
BFF-forwarded GoTrue DELETE, error-code mapping, audit.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001
from fastapi.testclient import TestClient

import tortoise.hosted_api as ha_mod
import tortoise.session_auth as sa
import tortoise.supabase_control as sc
from tortoise.hosted_api import app
from tests.fake_control_plane import FakeControlPlane
from datetime import UTC


class _FakeResp:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://authtest.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-auth-test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-test")
    monkeypatch.setenv("TORTOISE_LINK_INTENT_SECRET", "test-secret")
    monkeypatch.setenv("TORTOISE_MANUAL_LINKING_ENABLED", "1")
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
    fake = FakeControlPlane()
    monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
    # default: fresh session, confirmed email, no identities
    async def _fake_verify(request):
        return {"user_id": "u-1", "email": "u@example.com",
                "app_metadata": {"providers": ["github"]}}
    monkeypatch.setattr(sa, "verify_session_jwt", _fake_verify)
    from datetime import datetime, timedelta
    _fresh = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
    monkeypatch.setattr(ha_mod, "_identity_admin_user",
                        lambda uid: {"id": uid, "email": "u@example.com",
                                     "email_confirmed_at": "2026-08-01T00:00:00+00:00",
                                     "last_sign_in_at": _fresh,
                                     "identities": []})
    yield fake
    ha_mod._id_rate_buckets.clear()


@pytest.fixture
def fake(_env):
    return _env


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _seed_admin(monkeypatch, *, identities=None, confirmed=True,
                last_sign_in=None, email="u@example.com"):
    if last_sign_in is None:
        from datetime import datetime, timedelta
        last_sign_in = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
    monkeypatch.setattr(ha_mod, "_identity_admin_user",
                        lambda uid: {"id": uid, "email": email,
                                     "email_confirmed_at": "2026-08-01T00:00:00+00:00" if confirmed else None,
                                     "last_sign_in_at": last_sign_in,
                                     "identities": identities or []})


# ── link-intent ────────────────────────────────────────────────────────────
def test_link_intent_503_when_secret_unset(client, monkeypatch):
    monkeypatch.delenv("TORTOISE_LINK_INTENT_SECRET")
    r = client.post("/v1/user/identity/link-intent", json={"provider": "github"})
    assert r.status_code == 503


def test_link_intent_503_when_linking_off(client, monkeypatch):
    monkeypatch.setenv("TORTOISE_MANUAL_LINKING_ENABLED", "0")
    r = client.post("/v1/user/identity/link-intent", json={"provider": "github"})
    assert r.status_code == 503


def test_link_intent_reauth_required(client, monkeypatch):
    _seed_admin(monkeypatch, last_sign_in="2026-08-01T00:00:00+00:00")
    r = client.post("/v1/user/identity/link-intent", json={"provider": "github"})
    assert r.status_code == 403
    assert "REAUTH_REQUIRED" in r.text


def test_link_intent_already_linked(client, monkeypatch):
    _seed_admin(monkeypatch, identities=[{"provider": "github", "provider_id": "g1"}])
    r = client.post("/v1/user/identity/link-intent", json={"provider": "github"})
    assert r.status_code == 409


def test_link_intent_ok(client, monkeypatch, fake):
    r = client.post("/v1/user/identity/link-intent", json={"provider": "github"})
    assert r.status_code == 200
    body = r.json()
    assert "intent_ref" in body and body["provider"] == "github"
    assert fake.tables["link_intents"]  # row stored (consumed-once backstop)
    assert fake.tables["link_intents"][0]["user_id"] == "u-1"


# ── link-commit ────────────────────────────────────────────────────────────
def _make_ref(user_id="u-1", provider="github", ttl_s=120):
    import base64
    import hashlib
    import hmac
    from datetime import datetime, timedelta
    secret = os.environ["TORTOISE_LINK_INTENT_SECRET"]
    nonce = "n" * 43
    expires = (datetime.now(UTC) + timedelta(seconds=ttl_s)).isoformat()
    payload = f"{user_id}|{provider}|{nonce}|{expires}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=") + "." + sig


def test_link_commit_bad_signature(client):
    r = client.post("/v1/user/identity/link-commit",
                    json={"intent_ref": "garbage.sig"})
    assert r.status_code == 422


def test_link_commit_ok(client, monkeypatch, fake):
    ref = _make_ref()
    # store the intent row so consume succeeds
    import base64
    payload_b64, _ = ref.rsplit(".", 1)
    payload = base64.urlsafe_b64decode(payload_b64 + "==").decode()
    _uid, provider, nonce, expires = payload.split("|")
    fake.tables.setdefault("link_intents", []).append(
        {"nonce": nonce, "user_id": _uid, "provider": provider,
         "expires_at": expires, "consumed_at": None})
    # admin now shows the new identity (created after intent issuance)
    from datetime import datetime as _dt
    _seed_admin(monkeypatch, identities=[{
        "provider": "github", "provider_id": "gh-new",
        "created_at": _dt.now(UTC).isoformat(),
        "identity_data": {"email": "u@example.com"}}])
    r = client.post("/v1/user/identity/link-commit", json={"intent_ref": ref})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["linked"] is True and body["already"] is False
    # consumed-once: the row is marked consumed
    assert fake.tables["link_intents"][0]["consumed_at"] is not None


def test_link_commit_expired_degrades_to_already_linked(client, monkeypatch, fake):
    # expired intent + matching identity now present → graceful already-linked
    ref = _make_ref(ttl_s=-60)
    import base64
    payload_b64, _ = ref.rsplit(".", 1)
    payload = base64.urlsafe_b64decode(payload_b64 + "==").decode()
    _uid, provider, nonce, expires = payload.split("|")
    fake.tables.setdefault("link_intents", []).append(
        {"nonce": nonce, "user_id": _uid, "provider": provider,
         "expires_at": expires, "consumed_at": None})
    from datetime import datetime as _dt2
    _seed_admin(monkeypatch, identities=[{"provider": "github", "provider_id": "gh-x",
                                          "created_at": _dt2.now(UTC).isoformat()}])
    r = client.post("/v1/user/identity/link-commit", json={"intent_ref": ref})
    assert r.status_code == 200, r.text
    assert r.json()["already"] is True


def test_link_commit_unconfirmed_email_403(client, monkeypatch, fake):
    ref = _make_ref()
    import base64
    payload_b64, _ = ref.rsplit(".", 1)
    payload = base64.urlsafe_b64decode(payload_b64 + "==").decode()
    _uid, provider, nonce, expires = payload.split("|")
    fake.tables.setdefault("link_intents", []).append(
        {"nonce": nonce, "user_id": _uid, "provider": provider,
         "expires_at": expires, "consumed_at": None})
    from datetime import datetime as _dt
    _seed_admin(monkeypatch, confirmed=False, identities=[{
        "provider": "github", "provider_id": "gh-y",
        "created_at": _dt.now(UTC).isoformat()}])
    r = client.post("/v1/user/identity/link-commit", json={"intent_ref": ref})
    assert r.status_code == 403


# ── unlink ─────────────────────────────────────────────────────────────────
def test_unlink_reauth_required(client, monkeypatch):
    _seed_admin(monkeypatch, last_sign_in="2026-08-01T00:00:00+00:00")
    r = client.post("/v1/user/identity/unlink", json={"identity_id": "00000000-0000-0000-0000-000000000001"})
    assert r.status_code == 403


def test_unlink_floor_violated(client, monkeypatch, fake):
    # 1 method below the floor (github + confirmed email = 2; removing one
    # leaves 1 < 2) → reserve raises floor_violated → 409
    _seed_admin(monkeypatch, identities=[{"provider": "github", "provider_id": "g1"}])
    fake.auth_users = [{"id": "u-1", "email": "u@example.com",
                        "email_confirmed_at": "2026-08-01T00:00:00Z",
                        "encrypted_password": None}]
    fake.auth_identities = [{"id": "00000000-0000-0000-0000-0000000000a1", "user_id": "u-1",
                             "provider": "github", "provider_id": "gh1"}]
    r = client.post("/v1/user/identity/unlink",
                    json={"identity_id": "00000000-0000-0000-0000-0000000000a1"})
    assert r.status_code == 409


def test_unlink_success(client, monkeypatch, fake):
    import httpx
    calls = {}
    def _delete(url, headers=None, timeout=None):
        calls["url"] = url
        return _FakeResp(204)
    monkeypatch.setattr(httpx, "delete", _delete)
    # 3 methods: 2 oauth + confirmed email
    fake.auth_users = [{"id": "u-1", "email": "u@example.com",
                        "email_confirmed_at": "2026-08-01T00:00:00Z",
                        "encrypted_password": None}]
    fake.auth_identities = [{"id": "00000000-0000-0000-0000-00000000000a", "user_id": "u-1", "provider": "github",
                             "provider_id": "g1"},
                            {"id": "00000000-0000-0000-0000-00000000000b", "user_id": "u-1", "provider": "google",
                             "provider_id": "g2"}]
    r = client.post("/v1/user/identity/unlink", json={"identity_id": "00000000-0000-0000-0000-00000000000a"})
    assert r.status_code == 200, r.text
    assert r.json()["unlinked"] is True
    assert "identities/00000000-0000-0000-0000-00000000000a" in calls["url"]          # BFF-forwarded DELETE
    assert fake.tables["user_unlink_permits"][0]["consumed_at"] is not None  # consumed


def test_unlink_422_single_identity_surfaced(client, monkeypatch, fake):
    import httpx
    def _delete(url, headers=None, timeout=None):
        return _FakeResp(422, "single_identity_not_deletable")
    monkeypatch.setattr(httpx, "delete", _delete)
    fake.auth_users = [{"id": "u-1", "email": "u@example.com",
                        "email_confirmed_at": "2026-08-01T00:00:00Z",
                        "encrypted_password": None}]
    fake.auth_identities = [{"id": "00000000-0000-0000-0000-00000000000a", "user_id": "u-1", "provider": "github",
                             "provider_id": "g1"},
                            {"id": "00000000-0000-0000-0000-00000000000b", "user_id": "u-1", "provider": "google",
                             "provider_id": "g2"}]
    r = client.post("/v1/user/identity/unlink", json={"identity_id": "00000000-0000-0000-0000-00000000000a"})
    assert r.status_code == 409
    assert "another linked login method" in r.text
    # compensated: no pending permit left (no deadlock)
    assert not any(p["consumed_at"] is None for p in fake.tables.get("user_unlink_permits", []))


def test_unlink_404_already(client, monkeypatch, fake):
    import httpx
    def _delete(url, headers=None, timeout=None):
        return _FakeResp(404)
    monkeypatch.setattr(httpx, "delete", _delete)
    fake.auth_users = [{"id": "u-1", "email": "u@example.com",
                        "email_confirmed_at": "2026-08-01T00:00:00Z",
                        "encrypted_password": None}]
    fake.auth_identities = [{"id": "00000000-0000-0000-0000-00000000000a", "user_id": "u-1", "provider": "github",
                             "provider_id": "g1"},
                            {"id": "00000000-0000-0000-0000-00000000000b", "user_id": "u-1", "provider": "google",
                             "provider_id": "g2"}]
    r = client.post("/v1/user/identity/unlink", json={"identity_id": "00000000-0000-0000-0000-00000000000a"})
    assert r.status_code == 200
    assert r.json()["already"] is True


def test_unlink_httpx_failure_compensates(client, monkeypatch, fake):
    import httpx
    def _delete(url, headers=None, timeout=None):
        raise httpx.ConnectError("boom")
    monkeypatch.setattr(httpx, "delete", _delete)
    fake.auth_users = [{"id": "u-1", "email": "u@example.com",
                        "email_confirmed_at": "2026-08-01T00:00:00Z",
                        "encrypted_password": None}]
    fake.auth_identities = [{"id": "00000000-0000-0000-0000-00000000000a", "user_id": "u-1", "provider": "github",
                             "provider_id": "g1"},
                            {"id": "00000000-0000-0000-0000-00000000000b", "user_id": "u-1", "provider": "google",
                             "provider_id": "g2"}]
    r = client.post("/v1/user/identity/unlink", json={"identity_id": "00000000-0000-0000-0000-00000000000a"})
    assert r.status_code == 502
    assert not any(p["consumed_at"] is None for p in fake.tables.get("user_unlink_permits", []))


# ── resend-confirmation ────────────────────────────────────────────────────
def test_resend_already_confirmed_noop(client, monkeypatch):
    r = client.post("/v1/user/identity/resend-confirmation", json={})
    assert r.status_code == 200
    assert r.json()["already_confirmed"] is True


def test_resend_sends(client, monkeypatch):
    import httpx
    calls = {}
    def _post(url, json=None, headers=None, timeout=None):
        calls["url"] = url
        return _FakeResp(200)
    monkeypatch.setattr(httpx, "post", _post)
    _seed_admin(monkeypatch, confirmed=False)
    r = client.post("/v1/user/identity/resend-confirmation", json={})
    assert r.status_code == 200
    assert r.json()["sent"] is True
    assert "resend" in calls["url"]


# ── review-fix additions (test-review cycle): two-tab, audit, BFF, 401s,
#    link-commit negatives, reauth-422, 429 ────────────────────────────────

def _seed_methods(fake, uid="u-1", *, n=3, confirmed=True):
    """Seed the fake auth rows so inventory reports n login methods."""
    fake.auth_users = [{"id": uid, "email": f"{uid}@example.com",
                        "email_confirmed_at": "2026-08-01T00:00:00Z" if confirmed else None,
                        "encrypted_password": None}]
    fake.auth_identities = [
        {"id": f"00000000-0000-0000-0000-{i:012d}", "user_id": uid,
         "provider": ("github" if i % 2 else "google"),
         "provider_id": f"p{i}"} for i in range(n - 1)]
    # n-1 oauth identities + confirmed-email method = n


def test_unlink_two_tab_threaded(client, monkeypatch, fake):
    """Journey step 6: two concurrent unlinks for the same user → exactly
    one succeeds, the other 409 (one-pending-permit invariant + unique-
    index parity in the fake)."""
    import threading

    import httpx
    results = {}
    _seed_methods(fake, n=3)

    def _delete(url, headers=None, timeout=None):
        # mirror GoTrue: the DELETE removes the identity (the post-verify
        # inventory must recompute login_methods AFTER removal)
        iid = url.rsplit("/", 1)[-1]
        fake.auth_identities[:] = [i for i in fake.auth_identities
                                   if i.get("id") != iid]
        return _FakeResp(204)
    monkeypatch.setattr(httpx, "delete", _delete)

    def do_unlink(iid):
        results[iid] = client.post("/v1/user/identity/unlink",
                                   json={"identity_id": iid}).status_code

    t1 = threading.Thread(target=do_unlink, args=("00000000-0000-0000-0000-000000000000",))
    t2 = threading.Thread(target=do_unlink, args=("00000000-0000-0000-0000-000000000001",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    codes = sorted(results.values())
    assert codes == [200, 409], f"expected one 200 + one 409, got {codes}"


def test_audit_rows_written(client, monkeypatch, fake):
    """Surface 15: identity_link / identity_unlink / identity_confirm_resend
    audit rows must be written (server-authority trail)."""
    import base64
    from datetime import datetime

    import httpx
    audit = []
    async def _capture_audit(request, team_id, operation, **kw):
        audit.append((operation, kw.get("detail")))
    monkeypatch.setattr(ha_mod, "_async_audit", _capture_audit)

    # link-commit (fresh intent + new identity) → identity_link
    ref = _make_ref()
    payload_b64, _ = ref.rsplit(".", 1)
    payload = base64.urlsafe_b64decode(payload_b64 + "==").decode()
    _uid, provider, nonce, expires = payload.split("|")
    fake.tables.setdefault("link_intents", []).append(
        {"nonce": nonce, "user_id": _uid, "provider": provider,
         "expires_at": expires, "consumed_at": None})
    _seed_admin(monkeypatch, identities=[{
        "provider": "github", "provider_id": "gh-aud",
        "created_at": datetime.now(UTC).isoformat(),
        "identity_data": {"email": "u@example.com"}}])
    r = client.post("/v1/user/identity/link-commit", json={"intent_ref": ref})
    assert r.status_code == 200
    assert any(op == "identity_link" for op, _ in audit)

    # unlink → identity_unlink (with remaining count)
    audit.clear()
    _seed_methods(fake, n=3)
    monkeypatch.setattr(httpx, "delete", lambda *a, **k: _FakeResp(204))
    r = client.post("/v1/user/identity/unlink", json={"identity_id": "00000000-0000-0000-0000-000000000000"})
    assert r.status_code == 200
    assert any(op == "identity_unlink" for op, _ in audit)

    # resend → identity_confirm_resend
    audit.clear()
    _seed_admin(monkeypatch, confirmed=False)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResp(200))
    r = client.post("/v1/user/identity/resend-confirmation", json={})
    assert r.status_code == 200
    assert any(op == "identity_confirm_resend" for op, _ in audit)


def test_unlink_bff_forwards_user_token_not_service_key(client, monkeypatch, fake):
    """The GoTrue DELETE must run as the USER (BFF): forward the request's
    Authorization token + anon apikey — NEVER the service-role key."""
    import httpx
    captured = {}
    def _delete(url, headers=None, timeout=None):
        captured.update(headers or {})
        return _FakeResp(204)
    monkeypatch.setattr(httpx, "delete", _delete)
    _seed_methods(fake, n=3)
    client.post("/v1/user/identity/unlink", json={"identity_id": "00000000-0000-0000-0000-000000000000"},
                headers={"Authorization": "Bearer user-token-abc"})
    assert captured.get("Authorization") == "Bearer user-token-abc"
    assert captured.get("apikey") == "anon-test"
    assert "svc-auth-test" not in str(captured)  # service key never leaks


def test_resend_body_is_signup_type(client, monkeypatch):
    import httpx
    captured = {}
    def _post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return _FakeResp(200)
    monkeypatch.setattr(httpx, "post", _post)
    _seed_admin(monkeypatch, confirmed=False)
    client.post("/v1/user/identity/resend-confirmation", json={})
    assert captured["json"] == {"type": "signup", "email": "u@example.com"}


def test_link_commit_negatives(client, monkeypatch, fake):
    """Ownership mismatch, no-new-identity, expired-no-identity → 422."""
    # ownership: ref for a DIFFERENT user
    ref = _make_ref(user_id="someone-else")
    r = client.post("/v1/user/identity/link-commit", json={"intent_ref": ref})
    assert r.status_code == 422
    # no-new-identity: valid ref, intent stored, but admin has NO matching identity
    ref2 = _make_ref()
    import base64
    payload_b64, _ = ref2.rsplit(".", 1)
    payload = base64.urlsafe_b64decode(payload_b64 + "==").decode()
    _uid, provider, nonce, expires = payload.split("|")
    fake.tables.setdefault("link_intents", []).append(
        {"nonce": nonce, "user_id": _uid, "provider": provider,
         "expires_at": expires, "consumed_at": None})
    _seed_admin(monkeypatch, identities=[])  # no github identity yet
    r = client.post("/v1/user/identity/link-commit", json={"intent_ref": ref2})
    assert r.status_code == 422
    # expired with no matching identity → 422 (not the graceful path)
    ref3 = _make_ref(ttl_s=-60)
    payload_b64, _ = ref3.rsplit(".", 1)
    payload = base64.urlsafe_b64decode(payload_b64 + "==").decode()
    _uid, provider, nonce, expires = payload.split("|")
    fake.tables.setdefault("link_intents", []).append(
        {"nonce": nonce, "user_id": _uid, "provider": provider,
         "expires_at": expires, "consumed_at": None})
    _seed_admin(monkeypatch, identities=[])
    r = client.post("/v1/user/identity/link-commit", json={"intent_ref": ref3})
    assert r.status_code == 422


def test_post_endpoints_require_session(client, monkeypatch, fake):
    """All four POST endpoints are session-only → 401 without a session."""
    import tortoise.session_auth as sa
    async def _no_session(request):
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Unauthorized")
    monkeypatch.setattr(sa, "verify_session_jwt", _no_session)
    for path, body in [
        ("/v1/user/identity/link-intent", {"provider": "github"}),
        ("/v1/user/identity/link-commit", {"intent_ref": "x.y"}),
        ("/v1/user/identity/unlink", {"identity_id": "00000000-0000-0000-0000-000000000001"}),
        ("/v1/user/identity/resend-confirmation", {}),
    ]:
        r = client.post(path, json=body)
        assert r.status_code == 401, f"{path}: {r.status_code}"


def test_unlink_reauth_422_mapped(client, monkeypatch, fake):
    """GoTrue reauthentication_not_valid 422 → 403 REAUTH_REQUIRED + permit
    compensated (no deadlock)."""
    import httpx
    def _delete(url, headers=None, timeout=None):
        return _FakeResp(422, "reauthentication_not_valid")
    monkeypatch.setattr(httpx, "delete", _delete)
    _seed_methods(fake, n=3)
    r = client.post("/v1/user/identity/unlink", json={"identity_id": "00000000-0000-0000-0000-000000000000"})
    assert r.status_code == 403
    assert not any(p["consumed_at"] is None for p in fake.tables.get("user_unlink_permits", []))


def test_rate_limit_429(client, monkeypatch):
    """Per-user rate limit on link-intent → 429 with retry copy. The limit
    constant is read at module import — set the module attr, not the env."""
    monkeypatch.setattr(ha_mod, "_LINK_RATE_LIMIT", 1)
    r1 = client.post("/v1/user/identity/link-intent", json={"provider": "github"})
    assert r1.status_code == 200, r1.text
    r2 = client.post("/v1/user/identity/link-intent", json={"provider": "github"})
    assert r2.status_code == 429, r2.text
    assert "try again" in r2.text.lower()


# ── code-review regression tests (P0 + token-log hygiene) ──────────────────
def test_oauth_quota_fields_no_nameerror():
    """#1765 review P0: _quota_fields must not NameError on cp/team_id — the
    OAuth MCP auth boundary crashes without this."""
    import tortoise.oauth as oa
    from tests.fake_control_plane import FakeControlPlane
    cp = FakeControlPlane()
    cp.seed("teams", [{"id": "t-1", "email": "owner@x.com", "tier": "free"}])
    row = {"id": "t-1", "tier": "free", "email": "owner@x.com"}
    out = oa._quota_fields(cp, row)
    assert out["email"] == "owner@x.com"  # or None via fallback — never NameError


def test_unlink_token_never_in_logs(client, monkeypatch, fake, caplog):
    """#1765 review P2 (token-log hygiene): the forwarded bearer token must
    never appear in tortoise.api logs across success + failure paths."""
    import logging

    import httpx
    _seed_methods(fake, n=3)
    calls = {"n": 0}
    def _delete(url, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResp(204)          # success
        return _FakeResp(422, "single_identity_not_deletable")  # failure
    monkeypatch.setattr(httpx, "delete", _delete)
    with caplog.at_level(logging.DEBUG, logger="tortoise.api"):
        r1 = client.post("/v1/user/identity/unlink", json={"identity_id": "00000000-0000-0000-0000-000000000000"},
                         headers={"Authorization": "Bearer tok-super-secret-xyz"})
        assert r1.status_code == 200
        r2 = client.post("/v1/user/identity/unlink", json={"identity_id": "00000000-0000-0000-0000-000000000000"},
                         headers={"Authorization": "Bearer tok-super-secret-xyz"})
        assert r2.status_code in (409, 422, 502)
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "tok-super-secret-xyz" not in joined, "forwarded token leaked into logs"
