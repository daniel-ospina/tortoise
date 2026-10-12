"""HTTP journey test: invite → email hook → public info → accept (#307/#1177).

Registry (selfhost) path via TestClient + temp FalkorDBLite, mirroring
tests/test_invites_http.py fixtures. Email send is monkeypatched (no network).
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import pytest
from fastapi.testclient import TestClient

from tests._http_fixtures import patched_tortoise_sdk
from tortoise import email_notify
from tortoise.hosted_api import app, get_current_user

# #1719 (Task 3): org_memberships.user_id is a uuid column — real JWT
# subjects are UUIDs; non-UUID user_id literals are prod-impossible.
_U1 = "9f2c1a40-0000-4a00-8000-000000000001"
_U2 = "9f2c1a40-0000-4a00-8000-000000000002"


@pytest.fixture
def client():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _U1,
            "email": "owner@example.com",
        }
        # #2127: shared helper (tests._http_fixtures.patched_tortoise_sdk) —
        # patch __init__ → temp DB + #1950 TORTOISE_DB_PATH pin + close-then-
        # clear at enter; pop-pin → restore __init__ → deterministic anchor
        # close → clear overrides at exit (replaces the local
        # _patch/_restore_tortoise_sdk_init copies). The _REG_SDKS hold +
        # close stay here (direct SDK constructions, not keepalive anchors).
        with patched_tortoise_sdk(db_path):
            try:
                with TestClient(app) as tc:
                    yield tc
            finally:
                while _REG_SDKS:
                    try:  # noqa: SIM105
                        _REG_SDKS.pop().close()
                    except Exception:
                        pass


# #1556: hold registry SDKs alive — _get_registry() drops the SDK ref;
# close-on-GC (#1475) shuts the temp server before the test uses it.
_REG_SDKS: list = []


@pytest.fixture
def reg(client):
    from tortoise.hosted_api import _make_sdk
    sdk = _make_sdk(namespace="registry")
    _REG_SDKS.append(sdk)
    return sdk._get_registry()


def _seed_team(reg, org_id: str = "team-1"):
    reg.query(
        "CREATE (t:Team {id:$id, name:$id, tier:'team'})",
        params={"id": org_id},
    )


def _seed_membership(reg, org_id: str, user_id: str, role: str = "owner"):
    reg.query(
        "CREATE (m:Membership {user_id:$uid, org_id:$tid, role:$role, "
        "status:'active', created_at:'2026-08-01T00:00:00+00:00'})",
        params={"uid": user_id, "tid": org_id, "role": role},
    )


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_secret_key_123")
    monkeypatch.setenv("EMAIL_LINK_BASE_URL", "https://tortoise.premiselabs.co")
    email_notify._skip_logged.clear()
    yield


def test_full_journey_invite_info_accept(client, reg, monkeypatch):
    """invite → email scheduled with token → info returns variables → accept."""
    _seed_team(reg, "team-1")
    _seed_membership(reg, "team-1", _U1)

    scheduled = {}

    def fake_send(org_name, invitee_email, role, token, invitation_id, on_sent=None):
        scheduled.update(org_name=org_name, email=invitee_email, role=role,
                         token=token, iid=invitation_id, on_sent=on_sent)

    monkeypatch.setattr(email_notify, "send_invite_email", fake_send)

    # 1. Admin invites a member
    r = client.post("/v1/invites", json={"org_id": "team-1", "email": "bob@example.com", "role": "member"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "invited"
    token = body["token"]

    # 2. Email was scheduled with the real token + team name + role
    assert scheduled["token"] == token
    assert scheduled["org_name"] == "team-1"
    assert scheduled["role"] == "member"
    assert scheduled["email"] == "bob@example.com"

    # 3. Public info endpoint returns the display variables (team name + inviter)
    r = client.get(f"/v1/invites/info?token={token}")
    assert r.status_code == 200, r.text
    info = r.json()
    assert info["org_name"] == "team-1"
    assert info["inviter_email"] == "owner@example.com"  # captured from JWT at mint
    assert info["role"] == "member"
    assert "expires_at" in info

    # 4. Invitee (bob, different user) accepts — email-match guard passes
    from tortoise.hosted_api import app as _app
    _app.dependency_overrides[get_current_user] = lambda: {
        "user_id": _U2,
        "email": "bob@example.com",
    }
    r = client.post("/v1/invites/accept", json={"token": token})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "member"

    # 5. Membership is active
    rows = reg.query(
        "MATCH (m:Membership {org_id:$tid, user_id:$uid}) RETURN m.status",
        params={"tid": "team-1", "uid": _U2},
    ).result_set
    assert rows and rows[0][0] == "active"


def test_info_unknown_token_404(client, reg):
    _seed_team(reg, "team-1")
    _seed_membership(reg, "team-1", _U1)
    r = client.get("/v1/invites/info?token=nonexistent-token")
    assert r.status_code == 404
    assert r.json()["detail"] == "Invite not found or expired"


def test_info_consumed_token_404(client, reg, monkeypatch):
    """An accepted invite is no longer visible via info (no oracle)."""
    _seed_team(reg, "team-1")
    _seed_membership(reg, "team-1", _U1)

    def fake_send(*a, **k):
        pass

    monkeypatch.setattr(email_notify, "send_invite_email", fake_send)

    r = client.post("/v1/invites", json={"org_id": "team-1", "email": "bob@example.com"})
    token = r.json()["token"]

    from tortoise.hosted_api import app as _app
    _app.dependency_overrides[get_current_user] = lambda: {
        "user_id": _U2,
        "email": "bob@example.com",
    }
    assert client.post("/v1/invites/accept", json={"token": token}).status_code == 200
    assert client.get(f"/v1/invites/info?token={token}").status_code == 404


def test_invite_email_failure_does_not_fail_mint(client, reg, monkeypatch, caplog):
    _seed_team(reg, "team-1")
    _seed_membership(reg, "team-1", _U1)

    def boom(*a, **k):
        raise RuntimeError("resend down")

    monkeypatch.setattr(email_notify, "send_invite_email", boom)
    r = client.post("/v1/invites", json={"org_id": "team-1", "email": "bob@example.com"})
    assert r.status_code == 200  # mint survives email failure
    assert r.json()["status"] == "invited"


def test_info_link_host_never_from_host_header(client, reg, monkeypatch):
    """EMAIL_LINK_BASE_URL drives the link — never the request Host header."""
    _seed_team(reg, "team-1")
    _seed_membership(reg, "team-1", _U1)

    captured = {}

    def fake_send(org_name, invitee_email, role, token, invitation_id, on_sent=None):
        captured["link_base"] = None
        from tortoise.email_notify import _build_invite_link
        captured["link"] = _build_invite_link(token)

    monkeypatch.setattr(email_notify, "send_invite_email", fake_send)
    client.post("/v1/invites", json={"org_id": "team-1", "email": "bob@example.com"},
                headers={"Host": "evil.example.com"})
    assert captured["link"].startswith("https://tortoise.premiselabs.co/")


# ── #4588: the registry lane's public invite scan prunes on ``expires_at`` ──
#
# ``GET /v1/invites/info`` is UNAUTHENTICATED and every candidate row costs a
# full PBKDF2 (``verify_api_key`` → 100k iterations), so the row set the query
# returns IS the amplification surface an anonymous caller can drive. Nothing
# but accept, revoke, the admin ``expire_invite`` and the clock sweep writes a
# terminal ``status``, so an invite that expired by the clock alone stays
# 'pending' and was scanned+hashed on EVERY request. The tests below pin the
# three properties #4588 asks for: expired rows are pruned by the QUERY (never
# handed to the hash loop), the pinned predicate is what does it, and the
# NULL-expiry (Never) arm is not collateral damage — a lexicographic mismatch
# silently dropping valid invites was the issue's explicit warning.

_EXPIRED_AT = "2020-01-01T00:00:00+00:00"  # +00:00-offset ISO, the stored form


def _seed_pending_invite(reg, *, iid, email, token, expires_at, role="member"):
    """Seed a non-terminal ``Invitation`` the way both mints write it.

    ``expires_at`` is a ``+00:00``-offset ISO-8601 STRING (``invite_to_org``
    and ``TortoiseSDK.invitation_create`` both use
    ``datetime.now(UTC).isoformat()``); ``expires_at=None`` seeds a LEGACY node
    with the property ABSENT, the pre-expiry-props shape. Returns the stored
    ``token_hash`` so a test can assert it never reaches the verifier.
    """
    from tortoise.auth import hash_api_key

    stored = hash_api_key(token)
    props = ", expires_at:$exp" if expires_at is not None else ""
    params = {"id": iid, "tid": "team-1", "email": email, "th": stored}
    if expires_at is not None:
        params["exp"] = expires_at
    reg.query(
        "CREATE (i:Invitation {id:$id, org_id:$tid, email:$email, role:$role, "
        "token_hash:$th, inviter_email:'owner@example.com', "
        "created_at:'2026-01-01T00:00:00+00:00', accepted_at:null, "
        "status:'pending'" + props + "})",
        params={**params, "role": role},
    )
    return stored


class _RecordingRegistry:
    """Delegating wrapper recording every ``(cypher, params)`` the route runs.

    Patches the ``TortoiseSDK`` CLASS method rather than the fixture's
    projection instance: ``_get_registry`` caches PER SDK and the route builds
    its own via ``_make_sdk``, so an instance-level patch would only observe
    the handler if the fixture's SDK and the route's happened to be the same
    cached anchor — a fragile coupling, not a guarantee.
    """

    def __init__(self, inner):
        self._inner = inner

    def query(self, cypher, **kwargs):
        _RECORDED.append((cypher, kwargs.get("params") or {}))
        return self._inner.query(cypher, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


_RECORDED: list = []


def _record_registry_reads(monkeypatch):
    """Install the recorder; returns the shared ``(cypher, params)`` list."""
    from tortoise.sdk import TortoiseSDK

    real = TortoiseSDK._get_registry
    _RECORDED.clear()
    monkeypatch.setattr(TortoiseSDK, "_get_registry",
                        lambda self: _RecordingRegistry(real(self)))
    return _RECORDED


def test_invite_info_prunes_expired_rows_before_the_hash_loop(client, reg,
                                                              monkeypatch):
    """#4588: an expired invite never reaches ``verify_api_key``.

    Three expired-but-'pending' invites sit beside one live mint. The live
    token must still resolve AND exactly ONE row may reach the verifier: the
    expired rows are excluded by the query, not merely 404'd by the handler's
    post-match ``exp < now`` check (which runs only AFTER a match, i.e. after
    the PBKDF2 the amplification is made of).
    """
    _seed_team(reg, "team-1")
    _seed_membership(reg, "team-1", _U1)

    expired_tokens = [f"expired-token-{n}" for n in range(3)]
    expired_hashes = {
        _seed_pending_invite(reg, iid=f"inv-expired-{n}",
                             email=f"gone-{n}@example.com", token=tok,
                             expires_at=_EXPIRED_AT)
        for n, tok in enumerate(expired_tokens)
    }

    # A real mint, not a seed: the live row must carry the real write format
    # (its ``expires_at`` is ``now + 7d`` as ISO, which is what the predicate
    # compares against and what ``hash_api_key``'s random salt hides).
    monkeypatch.setattr(email_notify, "send_invite_email", lambda *a, **k: None)
    r = client.post("/v1/invites", json={"org_id": "team-1",
                                         "email": "bob@example.com"})
    assert r.status_code == 200, r.text
    live_token = r.json()["token"]

    import tortoise.auth

    seen: list[str] = []
    real_verify = tortoise.auth.verify_api_key

    def _recording_verify(token, stored):
        seen.append(stored)
        return real_verify(token, stored)

    captured = _record_registry_reads(monkeypatch)
    monkeypatch.setattr(tortoise.auth, "verify_api_key", _recording_verify)

    # 1. the live invite still resolves (the prune must not over-filter)
    r = client.get(f"/v1/invites/info?token={live_token}")
    assert r.status_code == 200, r.text
    assert r.json()["org_name"] == "team-1"
    assert len(seen) == 1, (
        f"exactly one candidate row may reach the verifier — got {len(seen)}; "
        "an expired row reached the PBKDF2 loop (#4588)")
    assert real_verify(live_token, seen[0]), "the verified row is the live invite"
    assert not (set(seen) & expired_hashes)

    # 2. an expired token is a 404, and the ONLY candidate it verifies is the
    #    live row above — the expired rows never reach the hash loop. Without
    #    the prune each request verifies all four rows (live + 3 expired).
    before = len(seen)
    r = client.get(f"/v1/invites/info?token={expired_tokens[0]}")
    assert r.status_code == 404
    assert not (set(seen) & expired_hashes), (
        "an expired row was handed to the verifier — the expiry prune is "
        "missing from the query (#4588)")
    assert len(seen) - before == 1 and real_verify(live_token, seen[-1]), (
        "the candidate set must be the LIVE invites only")

    # 3. the prune is IN the Cypher (with a `now` param), not a Python-side
    #    filter, so the properties are not materialised at all.
    scans = [(c, p) for c, p in captured
             if "MATCH (i:Invitation)" in c and "i.token_hash" in c]
    assert scans, "the invite scan must run one Cypher read"
    cypher, params = scans[-1]
    assert "i.expires_at IS NULL OR i.expires_at > $now" in cypher, cypher
    assert isinstance(params.get("now"), str) and params["now"], params


def test_invite_info_still_resolves_a_legacy_null_expiry_invite(client, reg,
                                                                monkeypatch):
    """#4588: ``expires_at IS NULL`` = Never expires, and must keep resolving.

    Legacy nodes predate the expiry property. The predicate's ``IS NULL`` arm
    is what stops a None from being compared against the ``now`` string and
    silently dropping a VALID invite — the failure mode the issue called out.
    """
    _seed_team(reg, "team-1")
    _seed_membership(reg, "team-1", _U1)
    token = "legacy-null-expiry-token"
    _seed_pending_invite(reg, iid="inv-legacy", email="legacy@example.com",
                         token=token, expires_at=None, role="admin")

    r = client.get(f"/v1/invites/info?token={token}")
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "admin"
    assert r.json()["expires_at"] is None
