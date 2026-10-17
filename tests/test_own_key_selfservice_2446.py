"""#2446 (phase 2 of the #2427 permission model) — member SELF-SERVICE over a
credential they hold: atomic REPLACE + turn-off/on, and nothing else.

WHAT THIS FILE IS PINNING. `POST /v1/session/key` gains an optional
`replace_key_id`: the same call mints a same-class session replacement and
claim-revokes the displaced row (one-step, for the compromised-key moment —
brief §4.4). `PATCH /v1/team/keys/{id}` gains the member OWN-KEY slice
(enable/disable only). The authorization premise is the #2427 brief §4.2
HOLDER rule, expressed against today's schema (the `owner_user_id` column is
phase 3, #8692):

    holder(row) = created_by  iff created_via == 'bootstrap'   (self-service)
    holder(row) = None        otherwise                        (org-owned)

so the class-by-class matrix in this file IS the authorization contract:

  OWN (200)                    | NOT OWN (403, byte-identical POLICY A detail)
  -----------------------------|---------------------------------------------
  the caller's own bootstrap   | another member's bootstrap key
  session credential           | the owner's bootstrap key
                               | an ORG-OWNED durable row the caller MINTER
                               | (created_by == caller, but not self-service)

and the two regression pins that must NOT move:

  * `rotate` keeps its #4355 cap predicate verbatim — a member rotating even
    their OWN bootstrap row still 403s at the POLICY A gate (the brief §8
    "keep the 409 / distinct replace" decision). `DELETE` stays owner/admin.
  * every non-own-key surface reads the pre-#2446 403 detail — a distinct
    message would turn the gate into an ownership oracle.

Non-widening is pinned too (brief §2.3 item 1 / §8): the replacement inherits
the displaced row's ABSOLUTE expiry, so a replace can neither lengthen a
session credential nor be talked into one by the request body — the ephemeral
credential cannot be laundered into a longer-lived (or durable) one.

Lanes: Supabase (FakeControlPlane) is primary; the registry/selfhost twin gets
the same matrix at the end. Harness shapes are mirrored from
tests/test_session_key_recovery_gate.py (#2380) so the two files cannot drift
in how they emulate a session face.
"""
from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime, timedelta

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import tortoise.hosted_api as ha
import tortoise.supabase_control as sc
from tests._http_fixtures import patched_tortoise_sdk
from tests.fake_control_plane import FakeControlPlane
from tests.test_supabase_control import FREE_TEAM
from tortoise.hosted_api import app, get_current_user
from tortoise.sdk import TortoiseSDK

# #1719: real JWT subjects are UUIDs and api_keys.created_by mirrors the
# minting user's UUID (FakeControlPlane fidelity-checks the shape).
_U1 = "9f2c1a40-0000-4a00-8000-0000000000a1"   # the acting member
_U2 = "9f2c1a40-0000-4a00-8000-0000000000a2"   # another member
_UOWN = "9f2c1a40-0000-4a00-8000-0000000000a3"  # the owner

_ROLE_403 = "Requires owner or admin role in team"
_NOT_YOURS = "Not your API key"
_SB_TEAM = "team-free-001"   # the id FREE_TEAM is seeded under (recovery-gate parity)
_FAR_FUTURE = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
_FAR_FUTURE_2 = (datetime.now(UTC) + timedelta(hours=5)).isoformat()


def _assert_role_403(r):
    """The #2297 POLICY A 403, BYTE-IDENTICAL — the whole point of the slice is
    that a non-own row is indistinguishable from an ungated member attempt."""
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == _ROLE_403


def _bootstrap_rows(fake, org_id, user_id, *, live_only=False):
    rows = [k for k in fake.tables.get("api_keys", [])
            if k.get("org_id") == org_id
            and k.get("created_via") == "bootstrap"
            and k.get("created_by") == user_id]
    if live_only:
        rows = [k for k in rows if k.get("revoked_at") is None]
    return rows


# ═══════════════════════════════════════════════════════════════════════════
# Supabase-lane fixtures (mirrors tests/test_session_key_recovery_gate.py)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def as_user(monkeypatch):
    """Emulate a session JWT face for BOTH auth dependencies.

    `get_current_user` delegates to `verify_session_jwt`, and the team-keys
    session lanes resolve the org through it too — so patching the one seam
    makes the whole session face consistent (the recovery-gate file overrides
    the dependency; this file needs the org-session lane as well). The
    dependency override is kept so a test can act on either endpoint.
    """
    import tortoise.session_auth as sa

    def _set(user_id: str, email: str | None = None):
        async def _verify(_request):
            return {"user_id": user_id, "email": email or "u@example.com",
                    "sub": user_id}

        monkeypatch.setattr(sa, "verify_session_jwt", _verify)
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": user_id, "email": email}

    yield _set
    app.dependency_overrides.pop(get_current_user, None)


_SESSION_HEADERS = {"Authorization": "Bearer eyJ.sess"}


def _sb_membership(fake, org_id: str, user_id: str, role: str):
    fake.tables.setdefault("org_memberships", []).append({
        "id": f"mem-{org_id}-{user_id}-{role}",
        "org_id": org_id, "user_id": user_id, "role": role,
        "status": "active", "created_at": "2026-01-01T00:00:00Z",
        "identity": None, "lookup_hash": None,
    })


def _sb_team(fake, org_id: str, **overrides) -> dict:
    for t in fake.tables.setdefault("organizations", []):
        if t.get("id") == org_id:
            t.update(overrides)
            return t
    team = dict(FREE_TEAM, id=org_id)
    team.update(overrides)
    fake.tables["organizations"].append(team)
    return team


def _sb_key(fake, key_id: str, org_id: str, *, created_by,
            created_via: str | None = "bootstrap",
            expires_at: str | None = _FAR_FUTURE,
            revoked_at: str | None = None,
            created_at: str = "2026-01-01T00:00:00Z",
            enabled: bool = True) -> dict:
    """Seed an api_keys row directly (no mint-trigger side effects)."""
    row = {
        "id": key_id, "org_id": org_id, "key_prefix": f"tt_{key_id[:6]}",
        "lookup_hash": f"hash-{key_id}", "created_via": created_via,
        "created_by": created_by, "created_at": created_at,
        "expires_at": expires_at, "revoked_at": revoked_at,
        "enabled": enabled, "name": None, "scopes": [],
        "delegation_depth": None, "graph_id": None, "created_by_key_id": None,
    }
    fake.tables.setdefault("api_keys", []).append(row)
    return row


def _row(fake, key_id: str) -> dict:
    return next(r for r in fake.tables["api_keys"] if r.get("id") == key_id)


@pytest.fixture
def sb(monkeypatch):
    """Supabase-mode TestClient with a fake control plane + temp DB."""
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://own-key-2446.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_own_key_2446")
    fake = FakeControlPlane({
        "api_keys": [], "org_memberships": [], "organizations": [dict(FREE_TEAM)],
        "invitations": [], "abuse_events": [],
    })
    monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "own-key-sb.db")
        with patched_tortoise_sdk(db_path), TestClient(app) as tc:
            yield tc, fake


def _replace(tc, key_id, *, body=None):
    payload = {"purpose": "bootstrap", "replace_key_id": key_id}
    payload.update(body or {})
    return tc.post("/v1/session/key", json=payload)


def _mint(tc):
    return tc.post("/v1/session/key", json={"purpose": "bootstrap"})


# ═══════════════════════════════════════════════════════════════════════════
# 1. The replace succeeds — and stays inside the displaced row's lifetime
# ═══════════════════════════════════════════════════════════════════════════


class TestReplaceOwnKeySupabase:

    def test_member_replaces_own_bootstrap_key(self, sb, as_user):
        """THE acceptance case: one call, old session credential dead, new one
        live, same class, audit written."""
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "member")
        _sb_key(fake, "own-boot", _SB_TEAM, created_by=_U1,
                expires_at=_FAR_FUTURE)
        as_user(_U1)
        r = _replace(tc, "own-boot")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["replaced_key_id"] == "own-boot"
        assert body["replaced_revoked"] is True
        assert "warning" not in body
        assert body["key"].startswith("tt_")
        assert body["purpose"] == "bootstrap"
        # the old row is DEAD and the replacement is the caller's session class
        assert _row(fake, "own-boot")["revoked_at"] is not None
        new = _row(fake, body["key_id"])
        assert new["created_via"] == "bootstrap"
        assert new["created_by"] == _U1
        assert new["revoked_at"] is None
        # Same CLASS as the displaced row: the session-mint lane writes a
        # bootstrap row with no scopes/graph/delegation (the RPC's INSERT
        # names neither — DB defaults), so the replacement is the same
        # owner-class ephemeral credential and nothing wider.
        assert not new.get("scopes")
        assert new.get("delegation_depth") is None
        assert new.get("graph_id") is None
        # exactly one live session credential for the member — 1-for-1
        assert len(_bootstrap_rows(fake, _SB_TEAM, _U1, live_only=True)) == 1

    def test_replacement_inherits_the_displaced_absolute_expiry(self, sb, as_user):
        """NON-WIDENING (brief §2.3 item 1 / §8): the replacement expires at the
        displaced row's ABSOLUTE instant — not a fresh 24h. A 2h-remaining
        session credential must not become a 24h one through replace."""
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "member")
        _sb_key(fake, "own-boot", _SB_TEAM, created_by=_U1,
                expires_at=_FAR_FUTURE)
        as_user(_U1)
        body = _replace(tc, "own-boot").json()
        assert body["expires_at"] == _FAR_FUTURE, (
            "the replacement must inherit the displaced row's expiry instant"
        )
        new = _new_row(fake, "own-boot")
        assert new["expires_at"] == _FAR_FUTURE
        # and strictly shorter than the lane's own 24h span
        assert datetime.fromisoformat(new["expires_at"]) < \
            datetime.now(UTC) + timedelta(hours=24)

    def test_body_cannot_launder_a_longer_lifetime(self, sb, as_user):
        """The ephemeral credential must not be launderable through replace: a
        body asking for a year (or a durable Never) is not the lane's vocabulary,
        and the stored expiry is STILL the displaced instant."""
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "member")
        _sb_key(fake, "own-boot", _SB_TEAM, created_by=_U1,
                expires_at=_FAR_FUTURE)
        as_user(_U1)
        r = _replace(tc, "own-boot", body={"expires_in": 365})
        assert r.status_code == 200, r.text
        assert r.json()["expires_at"] == _FAR_FUTURE
        assert _new_row(fake, "own-boot")["expires_at"] == _FAR_FUTURE

    def test_replace_at_the_three_active_session_cap_succeeds(self, sb, as_user):
        """The backstop is net-zero for a replace: at the 3-active ceiling the
        displaced row is credited (+1) because the same call retires it — the
        #4355 released-slot argument, applied to the session lane. Without the
        credit the compromise remedy 429s exactly when the member has been
        re-minting, which is when they most need it."""
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "member")
        for i in range(3):
            _sb_key(fake, f"own-boot-{i}", _SB_TEAM, created_by=_U1,
                    expires_at=_FAR_FUTURE)
        as_user(_U1)
        r = _replace(tc, "own-boot-0")
        assert r.status_code == 200, r.text
        assert len(_bootstrap_rows(fake, _SB_TEAM, _U1, live_only=True)) == 3
        # and the plain mint is STILL refused at the ceiling — the credit is
        # scoped to the replace, never a blanket exemption.
        assert _mint(tc).status_code == 429

    def test_plain_mint_shape_is_unchanged(self, sb, as_user):
        """Additive-only: a session mint WITHOUT the new field keeps its
        byte-identical pre-#2446 response (no `replaced_*` keys leak in)."""
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "member")
        as_user(_U1)
        r = _mint(tc)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "replaced_key_id" not in body
        assert "replaced_revoked" not in body
        assert set(body) == {"key", "key_prefix", "expires_at", "org_id",
                             "purpose", "rotated", "rotated_key_prefix"}

    def test_replace_writes_the_rotate_audit_event(self, sb, as_user, monkeypatch):
        """The brief's 'audit visible' rule: a replace is not invisible. The
        `api_key_rotate` vocabulary is reused so the trail reads the same as the
        owner's durable rotate."""
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "member")
        _sb_key(fake, "own-boot", _SB_TEAM, created_by=_U1,
                expires_at=_FAR_FUTURE)
        events: list[dict] = []

        async def _fake_audit(request, org_id, operation, **kwargs):
            events.append({"org_id": org_id, "operation": operation, **kwargs})

        monkeypatch.setattr(ha, "_async_audit", _fake_audit)
        as_user(_U1)
        assert _replace(tc, "own-boot").status_code == 200
        ops = [e["operation"] for e in events]
        assert "api_key_mint" in ops
        assert "api_key_rotate" in ops, events
        rotate_ev = next(e for e in events if e["operation"] == "api_key_rotate")
        assert rotate_ev["detail"] == {"replaced_key_id": "own-boot",
                                       "replaced_revoked": True}


# ═══════════════════════════════════════════════════════════════════════════
# 2. Everyone and everything that is NOT the caller's own row → POLICY A
# ═══════════════════════════════════════════════════════════════════════════


class TestReplaceNonOwnRefusals:

    def test_another_members_bootstrap_key_403(self, sb, as_user):
        """The one-level-of-trust rule: holding a copy of a colleague's
        credential is not authority to destroy it."""
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "member")
        _sb_membership(fake, _SB_TEAM, _U2, "member")
        _sb_key(fake, "peer-boot", _SB_TEAM, created_by=_U2,
                expires_at=_FAR_FUTURE)
        before = len(fake.tables["api_keys"])
        as_user(_U1)
        r = _replace(tc, "peer-boot")
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == _NOT_YOURS
        assert len(fake.tables["api_keys"]) == before, "no key may be minted"
        assert _row(fake, "peer-boot")["revoked_at"] is None

    def test_owners_bootstrap_key_403(self, sb, as_user):
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "member")
        _sb_membership(fake, _SB_TEAM, _UOWN, "owner")
        _sb_key(fake, "owner-boot", _SB_TEAM, created_by=_UOWN,
                expires_at=_FAR_FUTURE)
        as_user(_U1)
        r = _replace(tc, "owner-boot")
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == _NOT_YOURS
        assert _row(fake, "owner-boot")["revoked_at"] is None

    def test_member_cannot_replace_a_durable_row_they_MINted(self, sb, as_user):
        """THE holder-vs-minter pin (#2427 §4.2). `created_by == caller` is NOT
        enough: a durable row is ORG-OWNED (holder NULL), so even the member who
        created it may not retire it — otherwise the class gate would be
        `created_by`, which is the premise the issue comment corrected."""
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "member")
        # created_by IS the acting member — and it must still refuse.
        _sb_key(fake, "own-durable", _SB_TEAM, created_by=_U1,
                created_via="recovery", expires_at=None)
        before = len(fake.tables["api_keys"])
        as_user(_U1)
        r = _replace(tc, "own-durable")
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == _NOT_YOURS
        assert len(fake.tables["api_keys"]) == before
        assert _row(fake, "own-durable")["revoked_at"] is None

    def test_legacy_null_created_by_row_403(self, sb, as_user):
        """A bootstrap-shaped row with no `created_by` (a legacy unowned
        credential) has NO holder — it fails closed rather than becoming
        self-manageable by whichever member tries first."""
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "member")
        _sb_key(fake, "legacy-boot", _SB_TEAM, created_by=None,
                expires_at=_FAR_FUTURE)
        as_user(_U1)
        r = _replace(tc, "legacy-boot")
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == _NOT_YOURS
        assert _row(fake, "legacy-boot")["revoked_at"] is None

    def test_api_attributed_row_403(self, sb, as_user):
        """`created_by == 'api'` is key-driven attribution, not a holder."""
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "member")
        _sb_key(fake, "api-boot", _SB_TEAM, created_by="api",
                expires_at=_FAR_FUTURE)
        as_user(_U1)
        r = _replace(tc, "api-boot")
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == _NOT_YOURS

    def test_foreign_org_row_403(self, sb, as_user):
        """Org scoping is checked independently of ownership — a real id from
        another org is refused with the shared ownership detail."""
        tc, fake = sb
        _sb_team(fake, "team-free-other")
        _sb_membership(fake, _SB_TEAM, _U1, "member")
        _sb_key(fake, "foreign-boot", "team-free-other", created_by=_U1,
                expires_at=_FAR_FUTURE)
        as_user(_U1)
        r = _replace(tc, "foreign-boot")
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == _NOT_YOURS
        assert _row(fake, "foreign-boot")["revoked_at"] is None

    def test_unknown_id_404(self, sb, as_user):
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "member")
        as_user(_U1)
        r = _replace(tc, "nope-2446")
        assert r.status_code == 404, r.text
        assert fake.tables["api_keys"] == []

    def test_revoked_target_409_nothing_minted(self, sb, as_user):
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "member")
        _sb_key(fake, "own-boot", _SB_TEAM, created_by=_U1,
                expires_at=_FAR_FUTURE,
                revoked_at="2026-01-02T00:00:00Z")
        before = len(fake.tables["api_keys"])
        as_user(_U1)
        r = _replace(tc, "own-boot")
        assert r.status_code == 409, r.text
        assert len(fake.tables["api_keys"]) == before

    def test_expired_target_409_nothing_minted(self, sb, as_user):
        """An expired row never authenticates (#742), so inheriting its expiry
        would mint an already-dead replacement. Refuse and say what to do."""
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "member")
        _sb_key(fake, "own-boot", _SB_TEAM, created_by=_U1,
                expires_at=(datetime.now(UTC) - timedelta(minutes=5)).isoformat())
        before = len(fake.tables["api_keys"])
        as_user(_U1)
        r = _replace(tc, "own-boot")
        assert r.status_code == 409, r.text
        assert "mint a new one" in r.json()["detail"]
        assert len(fake.tables["api_keys"]) == before

    def test_recovery_purpose_with_replace_is_422(self, sb, as_user):
        """`recovery` is durable AND POLICY A-gated (#2380). Refusing loudly
        matters: a silent ignore would let a member believe an old credential
        was retired while it stayed live."""
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "member")
        _sb_key(fake, "own-boot", _SB_TEAM, created_by=_U1,
                expires_at=_FAR_FUTURE)
        as_user(_U1)
        r = tc.post("/v1/session/key", json={
            "purpose": "recovery", "replace_key_id": "own-boot"})
        assert r.status_code == 422, r.text
        assert "replace_key_id" in r.json()["detail"]
        assert _row(fake, "own-boot")["revoked_at"] is None

    def test_non_string_replace_key_id_is_422(self, sb, as_user):
        """A typo must never degrade to 'no replace requested' — the member
        would be told the key is dead while it is live."""
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "member")
        as_user(_U1)
        r = tc.post("/v1/session/key", json={
            "purpose": "bootstrap", "replace_key_id": {"id": "own-boot"}})
        assert r.status_code == 422, r.text
        assert fake.tables["api_keys"] == []


# ═══════════════════════════════════════════════════════════════════════════
# 3. The lanes this issue must NOT have moved
# ═══════════════════════════════════════════════════════════════════════════


class TestPolicyAUnchanged:

    def test_member_rotate_of_own_bootstrap_still_403(self, sb, as_user):
        """THE separation pin. `rotate` keeps its #4355 cap predicate verbatim
        ('requires a live key that occupies a key slot', 409 for a cap-exempt
        bootstrap row) and a member reaches it only through the POLICY A gate —
        which still 403s. Replace is a DISTINCT path (the brief §8 decision),
        not a relaxation of rotate."""
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "member")
        _sb_key(fake, "own-boot", _SB_TEAM, created_by=_U1,
                expires_at=_FAR_FUTURE)
        as_user(_U1)
        r = tc.post("/v1/team/keys/own-boot/rotate", json={})
        _assert_role_403(r)
        assert _row(fake, "own-boot")["revoked_at"] is None

    def test_member_revoke_of_own_bootstrap_still_403(self, sb, as_user):
        """`DELETE` (terminal revoke) stays owner/admin — the issue's member
        verbs are replace + turn-off, and turn-off is a reversible disable."""
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "member")
        _sb_key(fake, "own-boot", _SB_TEAM, created_by=_U1,
                expires_at=_FAR_FUTURE)
        as_user(_U1)
        r = tc.delete("/v1/team/keys/own-boot")
        _assert_role_403(r)
        assert _row(fake, "own-boot")["revoked_at"] is None

    def test_owner_patch_of_a_bootstrap_row_still_409(self, sb, as_user):
        """The pre-existing 'Cannot modify a session key' guard is NARROWED,
        not removed: an owner/admin still gets it on a bootstrap row. Only the
        member who HOLDS the row gets the self-service slice."""
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _UOWN, "owner")
        _sb_key(fake, "owner-boot", _SB_TEAM, created_by=_UOWN,
                expires_at=_FAR_FUTURE)
        as_user(_UOWN)
        r = tc.patch("/v1/team/keys/owner-boot", json={"enabled": False})
        assert r.status_code == 409, r.text
        assert r.json()["detail"] == "Cannot modify a session key"
        assert _row(fake, "owner-boot")["enabled"] is True

    def test_owner_rotate_of_a_durable_row_is_unchanged(self, sb, as_user):
        """The owner lane of rotate still works byte-for-byte (a durable row at
        its cap credit) — the #2446 slice added no branch to it."""
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _UOWN, "owner")
        _sb_key(fake, "own-durable", _SB_TEAM, created_by=_UOWN,
                created_via="recovery", expires_at=None)
        as_user(_UOWN)
        r = tc.post("/v1/team/keys/own-durable/rotate", json={})
        assert r.status_code == 200, r.text
        assert r.json()["replaced_key_id"] == "own-durable"
        assert _row(fake, "own-durable")["revoked_at"] is not None


# ═══════════════════════════════════════════════════════════════════════════
# 4. Turn off / turn on (PATCH) — the second member verb
# ═══════════════════════════════════════════════════════════════════════════


class TestToggleOwnKeySupabase:

    def test_member_turns_off_and_back_on_own_bootstrap_key(self, sb, as_user):
        """'Turn off' is a reversible ENABLE/DISABLE, and the member who holds
        the credential may do it — the whole point of the affordance (an owner
        must not be needed to stop an agent using a leaked session key)."""
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "member")
        _sb_key(fake, "own-boot", _SB_TEAM, created_by=_U1,
                expires_at=_FAR_FUTURE)
        as_user(_U1)
        off = tc.patch("/v1/team/keys/own-boot", json={"enabled": False})
        assert off.status_code == 200, off.text
        assert off.json()["enabled"] is False
        assert _row(fake, "own-boot")["enabled"] is False
        on = tc.patch("/v1/team/keys/own-boot", json={"enabled": True})
        assert on.status_code == 200, on.text
        assert on.json()["enabled"] is True
        assert _row(fake, "own-boot")["enabled"] is True

    def test_member_toggle_of_another_members_key_403(self, sb, as_user):
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "member")
        _sb_membership(fake, _SB_TEAM, _U2, "member")
        _sb_key(fake, "peer-boot", _SB_TEAM, created_by=_U2,
                expires_at=_FAR_FUTURE)
        as_user(_U1)
        _assert_role_403(tc.patch("/v1/team/keys/peer-boot",
                                  json={"enabled": False}))
        assert _row(fake, "peer-boot")["enabled"] is True

    def test_member_toggle_of_the_owners_key_403(self, sb, as_user):
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "member")
        _sb_membership(fake, _SB_TEAM, _UOWN, "owner")
        _sb_key(fake, "owner-durable", _SB_TEAM, created_by=_UOWN,
                created_via="recovery", expires_at=None)
        as_user(_U1)
        _assert_role_403(tc.patch("/v1/team/keys/owner-durable",
                                  json={"enabled": False}))
        assert _row(fake, "owner-durable")["enabled"] is True

    def test_member_toggle_of_org_owned_row_they_minted_403(self, sb, as_user):
        """Same holder rule as replace — a durable row is org-owned even when
        the caller minted it."""
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "member")
        _sb_key(fake, "own-durable", _SB_TEAM, created_by=_U1,
                created_via="recovery", expires_at=None)
        as_user(_U1)
        _assert_role_403(tc.patch("/v1/team/keys/own-durable",
                                  json={"enabled": False}))
        assert _row(fake, "own-durable")["enabled"] is True

    def test_own_slice_is_enable_only(self, sb, as_user):
        """`name` / `scopes` stay owner/admin verbs — the ask is 'turn off',
        and a member must not be able to probe which fields their row accepts
        (the refusal is the POLICY A detail, not a field-shaped error)."""
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "member")
        _sb_key(fake, "own-boot", _SB_TEAM, created_by=_U1,
                expires_at=_FAR_FUTURE)
        as_user(_U1)
        _assert_role_403(tc.patch("/v1/team/keys/own-boot",
                                  json={"name": "mine"}))
        _assert_role_403(tc.patch("/v1/team/keys/own-boot",
                                  json={"scopes": ["graphs:read"]}))
        assert _row(fake, "own-boot")["name"] is None

    def test_owner_renames_on_the_owner_lane_is_unchanged(self, sb, as_user):
        """The owner/admin lane still accepts name/scopes — the own-slice guard
        must not have leaked into the role lanes."""
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _UOWN, "owner")
        _sb_key(fake, "own-durable", _SB_TEAM, created_by=_UOWN,
                created_via="recovery", expires_at=None)
        as_user(_UOWN)
        r = tc.patch("/v1/team/keys/own-durable", json={"name": "renamed"})
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "renamed"

    def test_revoked_row_still_409_for_the_own_slice(self, sb, as_user):
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "member")
        _sb_key(fake, "own-boot", _SB_TEAM, created_by=_U1,
                expires_at=_FAR_FUTURE, revoked_at="2026-01-02T00:00:00Z")
        as_user(_U1)
        r = tc.patch("/v1/team/keys/own-boot", json={"enabled": False})
        assert r.status_code == 409, r.text
        assert r.json()["detail"] == "Cannot modify a revoked key"


# ═══════════════════════════════════════════════════════════════════════════
# 5. Registry / selfhost lane parity
# ═══════════════════════════════════════════════════════════════════════════

# #1588: hold registry SDKs alive — the `reg` fixture returns _get_registry()
# but the SDK goes out of scope; with #1475 close-on-GC the shared server is
# shut down before the test uses the handle.
_REG_SDKS: list = []


@pytest.fixture
def reg_client(monkeypatch):
    monkeypatch.delenv("TORTOISE_CONTROL_PLANE", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "own-key-reg.db")
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


@pytest.fixture
def reg():
    sdk = TortoiseSDK(namespace="registry")
    _REG_SDKS.append(sdk)
    return sdk._get_registry()


def _seed_team(reg, org_id: str, tier: str = "free"):
    reg.query(
        "CREATE (t:Team {id:$id, name:$name, tier:$tier})",
        params={"id": org_id, "name": org_id, "tier": tier},
    )


def _seed_membership(reg, org_id: str, user_id: str, role: str,
                     status: str = "active"):
    reg.query(
        "CREATE (m:Membership {user_id:$uid, org_id:$tid, role:$role, "
        "status:$status, created_at:'2026-08-01T00:00:00+00:00'})",
        params={"uid": user_id, "tid": org_id, "role": role,
                "status": status},
    )


def _seed_key(reg, org_id: str, key_id: str, *, created_by, created_via,
              expires_at: str | None, revoked_at: str | None = None):
    reg.query(
        "CREATE (k:APIKey {id:$id, org_id:$tid, key_hash:'h', key_prefix:'tt_x', "
        "created_by:$cb, created_at:'2026-01-01T00:00:00+00:00', revoked_at:$ra, "
        "expires_at:$exp, created_via:$cv})",
        params={"id": key_id, "tid": org_id, "cb": created_by, "ra": revoked_at,
                "exp": expires_at, "cv": created_via},
    )


def _reg_rows(reg, org_id: str, user_id: str) -> list[dict]:
    rows = reg.query(
        "MATCH (k:APIKey {org_id:$tid, created_via:'bootstrap', created_by:$uid}) "
        "RETURN k.id, k.revoked_at, k.expires_at",
        params={"tid": org_id, "uid": user_id},
    ).result_set
    return [{"id": r[0], "revoked_at": r[1], "expires_at": r[2]} for r in rows]


class TestOwnKeyRegistryParity:
    """The selfhost lane implements the same three steps (resolve → mint with
    the displaced absolute expiry → claim-revoke) and the same refusals."""

    def test_member_replaces_own_bootstrap_key(self, reg_client, reg, as_user):
        tc = reg_client
        _seed_team(reg, "team-2446r")
        _seed_membership(reg, "team-2446r", _U1, "member")
        _seed_key(reg, "team-2446r", "own-boot", created_by=_U1,
                  created_via="bootstrap", expires_at=_FAR_FUTURE)
        as_user(_U1)
        r = _replace(tc, "own-boot")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["replaced_key_id"] == "own-boot"
        assert body["replaced_revoked"] is True
        assert body["expires_at"] == _FAR_FUTURE, (
            "the registry twin must inherit the absolute expiry too"
        )
        rows = _reg_rows(reg, "team-2446r", _U1)
        assert next(x for x in rows if x["id"] == "own-boot")["revoked_at"] is not None
        live = [x for x in rows if x["revoked_at"] is None]
        assert len(live) == 1 and live[0]["expires_at"] == _FAR_FUTURE

    def test_member_cannot_replace_the_owners_key(self, reg_client, reg, as_user):
        tc = reg_client
        _seed_team(reg, "team-2446r")
        _seed_membership(reg, "team-2446r", _U1, "member")
        _seed_membership(reg, "team-2446r", _UOWN, "owner")
        _seed_key(reg, "team-2446r", "owner-boot", created_by=_UOWN,
                  created_via="bootstrap", expires_at=_FAR_FUTURE)
        as_user(_U1)
        r = _replace(tc, "owner-boot")
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == _NOT_YOURS
        assert next(x for x in _reg_rows(reg, "team-2446r", _UOWN)
                    if x["id"] == "owner-boot")["revoked_at"] is None

    def test_member_turns_off_own_key_is_refused_not_echoed(self, reg_client, reg,
                                                           as_user):
        tc = reg_client
        _seed_team(reg, "team-2446r")
        _seed_membership(reg, "team-2446r", _U1, "member")
        _seed_key(reg, "team-2446r", "own-boot", created_by=_U1,
                  created_via="bootstrap", expires_at=_FAR_FUTURE)
        as_user(_U1)
        # #2446 review P2-4: this lane has no `enabled` column, so the historical
        # {"key_id", "enabled": True} echo (#1148) is a NO-OP. Answering 200 to a
        # member's "turn my key OFF" would report a security action that never
        # ran while the credential kept authenticating — so the member slice
        # refuses instead of lying, and the row is provably untouched.
        r = tc.patch("/v1/team/keys/own-boot", json={"enabled": False})
        assert r.status_code == 409, r.text
        assert "not available on this deployment" in r.json()["detail"]
        rows = _reg_rows(reg, "team-2446r", _U1)
        assert next(x for x in rows if x["id"] == "own-boot")["revoked_at"] is None
        # The ENABLE direction keeps the historical echo: the key is on, so a
        # 200 here states a fact rather than a hope.
        r2 = tc.patch("/v1/team/keys/own-boot", json={"enabled": True})
        assert r2.status_code == 200, r2.text
        assert r2.json() == {"key_id": "own-boot", "enabled": True}

    def test_member_toggle_of_a_peer_key_403(self, reg_client, reg, as_user):
        tc = reg_client
        _seed_team(reg, "team-2446r")
        _seed_membership(reg, "team-2446r", _U1, "member")
        _seed_membership(reg, "team-2446r", _U2, "member")
        _seed_key(reg, "team-2446r", "peer-boot", created_by=_U2,
                  created_via="bootstrap", expires_at=_FAR_FUTURE)
        as_user(_U1)
        _assert_role_403(tc.patch("/v1/team/keys/peer-boot",
                                  json={"enabled": False}))

    def test_removed_member_holding_their_own_row_403(self, reg_client, reg,
                                                      as_user):
        """#2446 review P2-1: `remove_member` sets the membership to 'removed'
        and does NOT revoke the member's api_keys, and key auth performs no
        membership check — so a removed member still holds an authenticating
        session row. Holding it is not admission: `_membership_role` only returns
        a role for an ACTIVE membership, so this stays the byte-identical
        POLICY A 403 instead of a way back in."""
        tc = reg_client
        _seed_team(reg, "team-2446r")
        _seed_membership(reg, "team-2446r", _U1, "member", status="removed")
        _seed_key(reg, "team-2446r", "own-boot", created_by=_U1,
                  created_via="bootstrap", expires_at=_FAR_FUTURE)
        as_user(_U1)
        _assert_role_403(tc.patch("/v1/team/keys/own-boot",
                                  json={"enabled": True}))

    def test_holder_with_no_membership_row_403(self, reg_client, reg, as_user):
        """Same gate, the other entry: the holder predicate alone must never
        admit a caller whose membership row is gone entirely."""
        tc = reg_client
        _seed_team(reg, "team-2446r")
        _seed_key(reg, "team-2446r", "own-boot", created_by=_U1,
                  created_via="bootstrap", expires_at=_FAR_FUTURE)
        as_user(_U1)
        _assert_role_403(tc.patch("/v1/team/keys/own-boot",
                                  json={"enabled": True}))


# ═══════════════════════════════════════════════════════════════════════════
# 5. The replacement's failure modes (T3 class) — same disclosure as rotate
# ═══════════════════════════════════════════════════════════════════════════


def test_lost_claim_rolls_the_replacement_back_and_409s(monkeypatch):
    """A concurrent revoke/replace that wins the CLAIM must never leave an
    orphan live credential behind: the replacement is compensated away and the
    caller is told to retry. Asserted by driving the helper directly, because
    the interleaving cannot be produced from the HTTP surface without a seam.
    """
    import asyncio

    calls: list[str] = []

    def _claim(org_id, key_id, now):
        calls.append(f"claim:{key_id}")
        return False

    def _force(org_id, key_id, now):
        calls.append(f"force:{key_id}")

    monkeypatch.setattr(ha, "_claim_key_revocation", _claim)
    monkeypatch.setattr(ha, "_force_revoke_key_row", _force)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(ha._claim_replace_leg(
            "team-x", "old-key", "new-key", "2026-01-01T00:00:00+00:00"))
    assert exc.value.status_code == 409
    assert "rolled back" in exc.value.detail
    assert calls == ["claim:old-key", "force:new-key"]


def test_claim_exception_never_claims_the_key_was_revoked(monkeypatch):
    """#2446 review P2-3: a claim that RAISES did not lose a race — it never
    ran, so the displaced row is still LIVE. The lost-claim 409 ("it was revoked
    while this replace ran") would then be a false statement about a credential
    the caller may be trying to kill, so the replacement is compensated away
    first and the outage surfaces as the #2380 503 instead."""
    import asyncio

    calls: list[str] = []

    def _boom(org_id, key_id, now):
        calls.append(f"claim:{key_id}")
        raise RuntimeError("control plane down")

    def _force(org_id, key_id, now):
        calls.append(f"force:{key_id}")

    monkeypatch.setattr(ha, "_claim_key_revocation", _boom)
    monkeypatch.setattr(ha, "_force_revoke_key_row", _force)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(ha._claim_replace_leg(
            "team-x", "old-key", "new-key", "2026-01-01T00:00:00+00:00"))
    assert exc.value.status_code == 503, exc.value
    assert "revoked" not in str(exc.value.detail).lower()
    # compensated BEFORE the refusal: no orphan live credential either way
    assert calls == ["claim:old-key", "force:new-key"]


def test_claim_exception_with_failed_compensation_discloses_both_live(monkeypatch):
    """The same raise with a failing rollback is the one case where the caller
    is told BOTH keys are live (never that the old one is gone)."""
    import asyncio

    def _boom(org_id, key_id, now):
        raise RuntimeError("control plane down")

    def _force_boom(org_id, key_id, now):
        raise RuntimeError("still down")

    monkeypatch.setattr(ha, "_claim_key_revocation", _boom)
    monkeypatch.setattr(ha, "_force_revoke_key_row", _force_boom)
    replaced_revoked, warning = asyncio.run(ha._claim_replace_leg(
        "team-x", "old-key", "new-key", "2026-01-01T00:00:00+00:00"))
    assert replaced_revoked is False
    assert warning and "both are currently active" in warning


def test_replace_target_read_outage_is_503_not_500(monkeypatch):
    """#2446 review P2-2: the displace-target read is a control-plane call, so
    an outage must degrade to the repo-standard #2380 503 rather than escaping
    the handler as a raw 500."""
    import asyncio

    def _boom(org_id, key_id):
        raise RuntimeError("control plane down")

    monkeypatch.setattr(ha, "_rotatable_key_row", _boom)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(ha._resolve_replace_target("team-x", "old-key", "user-1"))
    assert exc.value.status_code == 503, exc.value


def test_double_failure_discloses_both_live_keys(monkeypatch):
    """Both legs failing must not lose the live secret NOR hide the second live
    credential: the replacement is returned with a `warning` (rotate's #4355
    disclosure, reused verbatim so a member gets the owner's honesty)."""
    import asyncio

    monkeypatch.setattr(ha, "_claim_key_revocation",
                        lambda org_id, key_id, now: False)

    def _boom(org_id, key_id, now):
        raise RuntimeError("control plane down")

    monkeypatch.setattr(ha, "_force_revoke_key_row", _boom)
    replaced_revoked, warning = asyncio.run(ha._claim_replace_leg(
        "team-x", "old-key", "new-key", "2026-01-01T00:00:00+00:00"))
    assert replaced_revoked is False
    assert warning and "both are currently active" in warning


# ═══════════════════════════════════════════════════════════════════════════
# helper: the replacement row, without guessing its generated id
# ═══════════════════════════════════════════════════════════════════════════


def _new_row(fake, displaced_id: str) -> dict:
    """The live bootstrap row that is neither the displaced one nor a
    pre-seeded fixture row (every test seeds at most one other bootstrap)."""
    live = [k for k in fake.tables["api_keys"]
            if k.get("revoked_at") is None and k.get("id") != displaced_id]
    assert len(live) == 1, f"expected exactly one replacement, got {live}"
    return live[0]
