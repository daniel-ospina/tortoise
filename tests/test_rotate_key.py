"""#4355 — the replacement-aware rotate primitive: adversarial acceptance.

`POST /v1/team/keys/{key_id}/rotate` is a privileged endpoint that DESTROYS one
credential and MINTS another, and it carries a change to the `max_api_keys`
enforcement arithmetic. Its correctness is therefore an ATTACKER claim ("an
attacker cannot make it fail open"), so this file pins the declared threat
surface of the #4355 scoping comment, class by class:

  T1  rotate a foreign org's key                → 403, nothing written
  T2  exceed the cap via rotate                 → at N/N rotate 200, plain
      create 402; a spent row id is refused 409; each rotate leaves the live
      count constant
  T3  leave the caller with no key              → create-first; a lost
      destructive leg compensates; a double failure REVEALS the live secret
  T4  escalation via a delegated/scoped caller  → deleg=0 403; scoped without
      keys:manage 403; the caller gate precedes ANY target lookup
  T5  credit a slot the cap never charged       → revoked / expired /
      bootstrap rows 409; a NULL `created_via` durable row IS rotatable
  T6  privilege widening through the replacement→ class inherited from the
      target; `scopes`/`graph_id` in the body 422
  T7  concurrent rotate of one row              → the conditional-revoke CLAIM
      admits one winner; a loser rolls back and 409s

Supabase lane (FakeControlPlane) — the hosted E2E suite covers the registry
lane. Session-authentication is used where a test needs more than one live
credential (the anon org's derived tier caps `max_api_keys` at 1) or needs to
exercise the owner/admin session gates.
"""
from __future__ import annotations

import os
import sys
import threading
import uuid
from datetime import UTC, datetime, timedelta

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001
from fastapi.testclient import TestClient

import tortoise.hosted_api as ha_mod
import tortoise.supabase_control as sc
from tortoise.hosted_api import app
from tests.fake_control_plane import FakeControlPlane

_SUPABASE_URL = "https://rotate4355.supabase.co"
_SESSION_HEADERS = {"Authorization": "Bearer eyJ.sess"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", _SUPABASE_URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-rotate-4355")
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
    ha_mod._CLAIM_BUCKETS.clear()
    fake = FakeControlPlane()
    monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
    yield fake
    ha_mod._CLAIM_BUCKETS.clear()
    app.dependency_overrides.clear()


@pytest.fixture
def fake(_env):
    return _env


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ── helpers ────────────────────────────────────────────────────────────────

def _rows(fake, org_id: str | None = None) -> list[dict]:
    rows = list(fake.tables.get("api_keys", []))
    if org_id is not None:
        rows = [r for r in rows if r.get("org_id") == org_id]
    return rows


def _live(fake, org_id: str) -> list[dict]:
    return [r for r in _rows(fake, org_id) if r.get("revoked_at") is None]


def _signup_org(client, fake) -> dict:
    """Provision one anonymous org; return its key-auth credentials."""
    r = client.post("/v1/agent/signup", json={})
    assert r.status_code == 200, r.text
    org_id = r.json()["org_id"]
    rows = _rows(fake, org_id)
    assert rows, "agent_signup must write an api_keys row"
    return {"key": r.json()["key"], "org_id": org_id, "key_id": rows[0]["id"]}


def _auth(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def _patch_session(monkeypatch, user_id: str) -> None:
    import tortoise.session_auth as sa

    async def _verify(_request):
        return {"user_id": user_id, "email": "owner@example.com", "sub": user_id}

    monkeypatch.setattr(sa, "verify_session_jwt", _verify)


def _claimed_session_org(client, fake, monkeypatch) -> dict:
    """Provision + CLAIM an org for a fresh session user (owner).

    Claiming lifts the #1082 anon ceiling, so the org runs the `free` tier and
    `max_api_keys` is the pricing default (2) — enough headroom for the tests
    that need a live caller credential BESIDES the rotate target. Note the
    stored `max_api_keys` column is not part of `org_by_id`'s projection, so a
    test must treat the tier default as the cap.
    """
    from tortoise.auth import lookup_hash

    org = _signup_org(client, fake)
    user_id = str(uuid.uuid4())
    _patch_session(monkeypatch, user_id)
    sc.claim_membership(fake, lookup_hash=lookup_hash(org["key"]),
                        user_id=user_id, email="owner@example.com")
    org["user_id"] = user_id
    org["headers"] = _SESSION_HEADERS
    return org


def _revoke_row(fake, key_id: str) -> None:
    for row in fake.tables.get("api_keys", []):
        if row.get("id") == key_id:
            row["revoked_at"] = datetime.now(UTC).isoformat()
            return
    raise AssertionError(f"no api_keys row {key_id}")


def _seed_graph(fake, org_id: str, graph_id: str) -> str:
    """A custom (key-bindable) graph row — `_ensure_graph_exists` 404s a
    graph-bound mint unless the org owns a non-default, non-deleted row."""
    fake.seed("graphs", [{
        "id": graph_id, "org_id": org_id, "name": graph_id,
        "kind": "custom", "namespace": f"ns_{org_id}_{graph_id}",
        "status": "active", "created_at": datetime.now(UTC).isoformat(),
    }])
    return graph_id


def _row_by_id(fake, key_id: str) -> dict:
    return next(r for r in fake.tables.get("api_keys", []) if r.get("id") == key_id)


def _mint(client, headers, body) -> dict:
    r = client.post("/v1/team/keys", headers=headers, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _rotate(client, key_id, headers, body=None):
    return client.post(f"/v1/team/keys/{key_id}/rotate", headers=headers,
                       json=body or {})


# ── T2: the cap proof + the spent-slot refusals ─────────────────────────────

def test_at_cap_rotate_succeeds_and_plain_create_still_402s(client, fake):
    """THE acceptance pair. An anon org is AT its derived cap (1) the moment it
    is provisioned. A plain create must stay 402 — the naive fix (exempting the
    mint) would make this pass instead — and a 1-for-1 rotate must succeed and
    leave exactly one live key."""
    org = _signup_org(client, fake)
    h = _auth(org["key"])
    assert client.post("/v1/team/keys", headers=h, json={}).status_code == 402, (
        "the plain mint must keep its cap: rotate may not exempt POST /v1/team/keys"
    )
    r = _rotate(client, org["key_id"], h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["replaced_key_id"] == org["key_id"]
    assert body["replaced_revoked"] is True
    assert body["key"] and body["key"] != org["key"]
    assert len(_live(fake, org["org_id"])) == 1, "rotate must be 1-for-1"
    # the cap still binds the NEW credential
    assert client.post("/v1/team/keys", headers=_auth(body["key"]),
                       json={}).status_code == 402


def test_rotate_revoked_row_409_with_a_live_caller(client, fake, monkeypatch):
    """T2/T5: a revoked row frees no slot (the count never held it), so
    crediting it would mint a free key. Refuse 409 WITHOUT minting."""
    org = _claimed_session_org(client, fake, monkeypatch)
    _mint(client, org["headers"], {"name": "survivor"})
    _revoke_row(fake, org["key_id"])
    before = len(_rows(fake, org["org_id"]))
    r = _rotate(client, org["key_id"], org["headers"])
    assert r.status_code == 409, r.text
    assert len(_rows(fake, org["org_id"])) == before, "no replacement may exist"


def test_rotate_revoked_caller_key_still_auth_fails_closed(client, fake):
    """T3 control: with the caller's ONLY key revoked, the request cannot even
    authenticate — and nothing is written."""
    org = _signup_org(client, fake)
    _revoke_row(fake, org["key_id"])
    r = _rotate(client, org["key_id"], _auth(org["key"]))
    assert r.status_code == 401, r.text
    assert len(_rows(fake, org["org_id"])) == 1


def test_rotate_expired_row_409(client, fake, monkeypatch):
    """T5: an expired durable no longer counts toward the cap, so it cannot be
    credited either."""
    org = _claimed_session_org(client, fake, monkeypatch)
    _mint(client, org["headers"], {"name": "survivor"})
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    for row in fake.tables["api_keys"]:
        if row["id"] == org["key_id"]:
            row["expires_at"] = past
    r = _rotate(client, org["key_id"], org["headers"])
    assert r.status_code == 409, r.text


def test_rotate_bootstrap_row_409(client, fake):
    """T5: a bootstrap (24h session) credential is cap-EXEMPT (#4140). Rotating
    it would convert an exempt credential into a DURABLE one while crediting a
    slot the cap never charged — the exact over-exemption the count's
    NULL-tolerant bootstrap filter exists to prevent."""
    org = _signup_org(client, fake)
    fake.tables.setdefault("api_keys", []).append({
        "id": "key_boot_4355",
        "org_id": org["org_id"],
        "lookup_hash": "hash_boot_4355",
        "key_prefix": "tt_boot4355",
        "created_via": "bootstrap",
        "created_by": "api",
        "created_at": datetime.now(UTC).isoformat(),
        "revoked_at": None,
        "expires_at": (datetime.now(UTC) + timedelta(hours=24)).isoformat(),
        "scopes": [], "delegation_depth": None, "graph_id": None,
        "created_by_key_id": None, "name": "boot",
    })
    before = len(_rows(fake, org["org_id"]))
    r = _rotate(client, "key_boot_4355", _auth(org["key"]))
    assert r.status_code == 409, r.text
    assert len(_rows(fake, org["org_id"])) == before


def test_rotate_null_created_via_durable_row_succeeds(client, fake):
    """T5 (the other direction): a NULL/legacy `created_via` is a DURABLE row —
    the count's bootstrap exclusion is NULL-TOLERANT, so a legacy row occupies
    a slot and MUST be rotatable (fail-closed the other way would strand
    selfhost upgrades)."""
    org = _signup_org(client, fake)
    for row in fake.tables["api_keys"]:
        if row["id"] == org["key_id"]:
            row["created_via"] = None
    r = _rotate(client, org["key_id"], _auth(org["key"]))
    assert r.status_code == 200, r.text
    assert len(_live(fake, org["org_id"])) == 1


def test_repeated_rotate_keeps_the_live_count_constant(client, fake):
    """T2: rotating repeatedly must never grow the live count (each rotate
    consumes the slot it releases)."""
    org = _signup_org(client, fake)
    h = _auth(org["key"])
    kid = org["key_id"]
    live_counts = []
    for _ in range(4):
        r = _rotate(client, kid, h)
        assert r.status_code == 200, r.text
        kid = r.json()["id"]
        h = _auth(r.json()["key"])
        live_counts.append(len(_live(fake, org["org_id"])))
    assert live_counts == [1, 1, 1, 1], live_counts


def test_session_rotate_at_cap_succeeds_and_plain_create_402s(client, fake, monkeypatch):
    """The same acceptance pair on the SESSION lane (the dashboard's actual
    caller), and the lane the copy change targets."""
    org = _claimed_session_org(client, fake, monkeypatch)
    _mint(client, org["headers"], {"name": "second"})   # free cap = 2 → at cap
    assert client.post("/v1/team/keys", headers=org["headers"],
                       json={}).status_code == 402
    r = _rotate(client, org["key_id"], org["headers"], {"name": "rotated"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "rotated"
    assert len(_live(fake, org["org_id"])) == 2
    assert client.post("/v1/team/keys", headers=org["headers"],
                       json={}).status_code == 402


# ── T1: ownership ───────────────────────────────────────────────────────────

def test_rotate_foreign_org_key_403(client, fake):
    """T1: a valid-looking id in ANOTHER org is refused by the fail-closed
    `_ensure_key_in_pinned_org`, with nothing written on either side."""
    a = _signup_org(client, fake)
    b = _signup_org(client, fake)
    before_a = len(_rows(fake, a["org_id"]))
    before_b = len(_rows(fake, b["org_id"]))
    r = _rotate(client, b["key_id"], _auth(a["key"]))
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "Not your API key"
    assert len(_rows(fake, a["org_id"])) == before_a
    assert len(_rows(fake, b["org_id"])) == before_b


def test_rotate_unknown_id_404(client, fake):
    org = _signup_org(client, fake)
    r = _rotate(client, "key_does_not_exist_4355", _auth(org["key"]))
    assert r.status_code == 404, r.text


# ── T4: caller-class escalation ─────────────────────────────────────────────

def _override_org(overrides: dict) -> None:
    from tortoise.hosted_api import get_current_org_session

    def _dep():
        base = {
            "org_id": "team-rotate-4355",
            "tier": "free",
            "max_api_keys": 5,
            "max_users": 1, "max_graphs": 1, "max_points": 10000,
            "max_sessions": None,
            "key_id": None, "scopes": [], "legacy_full_access": True,
            "delegation_depth": None, "created_by_key_id": None,
            "session_user_id": None,
        }
        base.update(overrides)
        return base

    app.dependency_overrides[get_current_org_session] = _dep


def test_deleg0_caller_cannot_rotate(client, fake):
    """T4: a MINTED (deleg=0) key must not reach the rotate surface — it is
    the escalation root the C2 one-level-deep guard exists for. The gate fires
    before any target lookup (the id is deliberately nonexistent).

    The override carries `keys:manage` on purpose so the ONLY gate that can
    produce the 403 is the deleg guard — otherwise `_require_keys_manage`
    would mask it and the test would pass with the guard deleted.
    """
    _override_org({"key_id": "key_child_4355", "delegation_depth": 0,
                   "legacy_full_access": False, "scopes": ["keys:manage"]})
    r = client.post("/v1/team/keys/whatever/rotate", json={})
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["error_code"] == "KEY_NOT_USER_MINTED"


def test_scoped_key_without_keys_manage_cannot_rotate(client, fake):
    """T4: a scoped deleg-NULL key (least privilege) must not destroy/replace
    org keys — mirrors the revoke lane's `_require_keys_manage`."""
    _override_org({"key_id": "key_scoped_4355", "legacy_full_access": False,
                   "scopes": ["graphs:read"]})
    r = client.post("/v1/team/keys/whatever/rotate", json={})
    assert r.status_code == 403, r.text
    assert "keys:manage" in r.json()["detail"]


def test_scoped_keys_manage_caller_rotates_to_a_deleg0_child(client, fake, monkeypatch):
    """T4 (the escalation-relevant SUCCESS path): a REAL scoped deleg-NULL key
    carrying `keys:manage` — the only non-owner class that reaches rotate — is
    replaced by a deleg=0 CHILD of that caller, never by another owner-class
    credential. This is the one success path with escalation relevance, and it
    is asserted end to end rather than through a dependency override.

    Non-vacuity: each claim below is the guard the test names — the child-policy
    intersection (`final_scopes = [s for s in target_scopes if s in
    _MINTABLE_SCOPES] or ["graphs:read"]`), the deleg=0 stamp, and the lineage
    assignment (`caller_key_id = org["key_id"]`). Removing any of them reddens
    this test.
    """
    org = _claimed_session_org(client, fake, monkeypatch)
    gid = _seed_graph(fake, org["org_id"], "g_rot4355_caller")
    # The caller: a session-minted, graph-bound, SCOPED key. Its class is
    # deleg-NULL + scopes non-empty → legacy_full_access False, key_id set →
    # `is_owner_class` False in rotate. It carries the escalation scope
    # `keys:manage` (mintable only by the owner class), which is exactly why
    # the replacement must NOT inherit it verbatim.
    caller = _mint(client, org["headers"],
                   {"name": "caller", "graph_id": gid, "scopes": ["keys:manage"]})
    assert caller["delegation_depth"] is None, caller
    assert caller["scopes"] == ["keys:manage"], caller
    r = _rotate(client, caller["id"], _auth(caller["key"]))
    assert r.status_code == 200, r.text
    body = r.json()
    # The child policy filters the escalation scope away (an empty intersection
    # falls back to the child default — never to the target's allowance).
    assert body["delegation_depth"] == 0, body
    assert set(body["scopes"] or []) <= set(ha_mod._MINTABLE_SCOPES), (
        f"the non-owner replacement must hold only child-policy scopes: {body['scopes']}"
    )
    assert body["graph_id"] == gid, "the replacement inherits the target's graph"
    row = _row_by_id(fake, body["id"])
    assert row["created_by_key_id"] == caller["id"], (
        "the child's lineage must name the rotating caller, not be laundered to None"
    )
    assert row["delegation_depth"] == 0
    assert row["graph_id"] == gid


def test_member_session_cannot_rotate(client, fake, monkeypatch):
    """T4: the #2297 POLICY A owner/admin gate applies to the SESSION lane — a
    plain member must not rotate an org key."""
    org = _claimed_session_org(client, fake, monkeypatch)
    member_id = str(uuid.uuid4())
    fake.tables.setdefault("org_memberships", []).append(
        {"user_id": member_id, "org_id": org["org_id"], "role": "member",
         "status": "active"})
    _patch_session(monkeypatch, member_id)
    r = _rotate(client, org["key_id"], _SESSION_HEADERS,
                None)
    # the pin is implicit (single membership), so the request reaches the role
    # gate for the member's org
    assert r.status_code == 403, r.text
    assert "owner" in r.json()["detail"].lower() or "admin" in r.json()["detail"].lower()


# ── T6: no privilege widening ───────────────────────────────────────────────

def test_rotate_body_scopes_or_graph_id_422(client, fake):
    """T6: the body must not be able to set the replacement's class — the
    scoped-key → unrestricted-key escalation class (CVE-2024-37282 /
    CVE-2026-56216)."""
    org = _signup_org(client, fake)
    for body in ({"scopes": ["team:manage"]}, {"graph_id": "g_deadbeef"},
                 {"scopes": [], "graph_id": "g_deadbeef"}):
        r = _rotate(client, org["key_id"], _auth(org["key"]), body)
        assert r.status_code == 422, f"{body} → {r.status_code}: {r.text}"
    assert len(_live(fake, org["org_id"])) == 1, "nothing may have rotated"


def test_rotate_inherits_the_targets_scopes(client, fake, monkeypatch):
    """T6 (positive half): a scoped target is replaced by an equally-scoped
    one — rotate is a REPLACEMENT, not a silent promotion."""
    org = _claimed_session_org(client, fake, monkeypatch)
    scoped = _mint(client, org["headers"],
                   {"name": "scoped", "scopes": ["graphs:write"]})
    assert scoped["scopes"] == ["graphs:write"]
    r = _rotate(client, scoped["id"], org["headers"], {"name": "scoped"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scopes"] == ["graphs:write"], (
        "the replacement must inherit the displaced row's scopes, not widen them"
    )
    assert body["delegation_depth"] is None
    assert body["graph_id"] is None


def test_rotate_inherits_the_targets_graph_binding(client, fake, monkeypatch):
    """T6 (graph half): a GRAPH-BOUND target is replaced by an equally
    graph-bound key. Dropping `graph_id=target_graph` from the `_mint_key` call
    would silently re-scope the replacement onto the org-wide/default graph — a
    privilege widening (or, for a default-shape key, a silent demotion).

    Non-vacuity: the `graph_id` passed to `_mint_key` is the guard under test;
    removing it reddens this test.
    """
    org = _claimed_session_org(client, fake, monkeypatch)
    gid = _seed_graph(fake, org["org_id"], "g_rot4355_bound")
    target = _mint(client, org["headers"],
                   {"name": "graphed", "graph_id": gid, "scopes": ["graphs:write"]})
    assert target["graph_id"] == gid, target
    r = _rotate(client, target["id"], org["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["graph_id"] == gid, (
        "the replacement must inherit the displaced row's graph binding"
    )
    assert _row_by_id(fake, body["id"])["graph_id"] == gid, (
        "the STORED row must be graph-bound, not just the response envelope"
    )


def test_rotate_preserves_a_child_targets_delegation_and_lineage(client, fake, monkeypatch):
    """T6 (lineage half): a deleg=0 CHILD target is replaced by a deleg=0 child
    of the SAME parent — rotate must not launder a delegated credential into an
    owner-class one, nor drop the lineage that makes the child revocable with
    its parent.

    Non-vacuity: the owner-class branch's `delegation_depth = row.get(...)` and
    `caller_key_id = row.get('created_by_key_id')` are the guards; nulling
    either reddens this test.
    """
    org = _claimed_session_org(client, fake, monkeypatch)
    gid = _seed_graph(fake, org["org_id"], "g_rot4355_child")
    fake.tables.setdefault("api_keys", []).append({
        "id": "key_child_target_4355",
        "org_id": org["org_id"],
        "lookup_hash": "hash_child_target_4355",
        "key_prefix": "tk_child4355",
        "created_via": "provisioned",
        "created_by": "api",
        "created_at": datetime.now(UTC).isoformat(),
        "revoked_at": None,
        "expires_at": None,
        "name": "child",
        "graph_id": gid,
        "scopes": ["graphs:read"],
        "delegation_depth": 0,
        "created_by_key_id": "key_parent_4355",
    })
    r = _rotate(client, "key_child_target_4355", org["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["delegation_depth"] == 0, (
        "a deleg=0 child must not be rotated into an owner-class key"
    )
    row = _row_by_id(fake, body["id"])
    assert row["created_by_key_id"] == "key_parent_4355", (
        "the child's parent lineage must survive the rotation"
    )
    assert row["graph_id"] == gid


def test_rotate_inherits_expiry_when_the_body_omits_it(client, fake, monkeypatch):
    """T6: an omitted expiry must never WIDEN the replacement to a Never key —
    a Never key in place of an expiring one is more privilege than the target
    held."""
    org = _claimed_session_org(client, fake, monkeypatch)
    target = _mint(client, org["headers"],
                   {"name": "expiring", "expires_in": 30})
    r = _rotate(client, target["id"], org["headers"], {})
    assert r.status_code == 200, r.text
    assert r.json().get("expires_at") == target["expires_at"], (
        "a body-omitted expiry must inherit the target's, never become Never"
    )


def test_rotate_keeps_a_body_supplied_expiry(client, fake):
    """T6 positive control: an explicit expiry still rides through (the
    dashboard re-applies the old row's lifetime span)."""
    org = _signup_org(client, fake)
    r = _rotate(client, org["key_id"], _auth(org["key"]), {"expires_in": 30})
    assert r.status_code == 200, r.text
    assert r.json().get("expires_at"), "an explicit expires_in must be honoured"


# ── T3: never leave the caller with no key ──────────────────────────────────

def test_create_leg_failure_leaves_the_old_key_live(client, fake, monkeypatch):
    """T3: if the replacement cannot be created, NOTHING may be revoked."""
    org = _signup_org(client, fake)

    def _boom(_cp, _row):
        raise RuntimeError("Supabase unreachable (simulated)")

    monkeypatch.setattr(sc, "insert_api_key", _boom)
    # The insert failure propagates as a 500 (mirroring the plain mint path).
    with pytest.raises(RuntimeError):
        _rotate(client, org["key_id"], _auth(org["key"]))
    assert [x["id"] for x in _live(fake, org["org_id"])] == [org["key_id"]], (
        "a failed create must leave the ORIGINAL key live"
    )
    # and the original key still authenticates
    assert client.get("/v1/team/keys", headers=_auth(org["key"])).status_code == 200


def test_destructive_leg_failure_compensates_and_refuses(client, fake, monkeypatch):
    """T3: if the claim-revoke cannot be performed, the replacement is rolled
    back (no orphan live key) and the call fails — the caller keeps the key
    they had."""
    org = _signup_org(client, fake)

    def _boom(_cp, _key_id, _now=None):
        raise RuntimeError("Supabase unreachable (simulated)")

    monkeypatch.setattr(sc, "claim_api_key_revocation", _boom)
    r = _rotate(client, org["key_id"], _auth(org["key"]))
    assert r.status_code >= 400, r.text
    assert [x["id"] for x in _live(fake, org["org_id"])] == [org["key_id"]], (
        "the compensation must revoke the replacement, leaving the old key live"
    )


def test_double_failure_reveals_the_live_replacement(client, fake, monkeypatch):
    """T3 (worst case): if the claim fails AND the compensation fails, the
    replacement is LIVE — the response must still carry its plaintext (the only
    alternative is losing a secret nobody can see) and must say the old key is
    still active."""
    org = _signup_org(client, fake)

    def _boom(*_a, **_k):
        raise RuntimeError("Supabase unreachable (simulated)")

    monkeypatch.setattr(sc, "claim_api_key_revocation", _boom)
    monkeypatch.setattr(sc, "revoke_api_key", _boom)
    r = _rotate(client, org["key_id"], _auth(org["key"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["replaced_revoked"] is False
    assert body.get("warning"), "the partial state must be stated"
    assert body["key"], "the live replacement must be revealed"
    assert len(_live(fake, org["org_id"])) == 2, "both keys are live — say so"


# ── T7: the concurrency claim ───────────────────────────────────────────────

def test_lost_claim_rolls_back_and_409s(client, fake, monkeypatch):
    """T7: the destructive leg is a CLAIM. When another writer already revoked
    the row, the loser must roll its replacement back and refuse — never leave
    an orphan live key behind, never exceed the cap at steady state.

    NOTE: this pins the rotate's behaviour GIVEN a lost-claim verdict; the
    verdict itself is pinned by `test_claim_api_key_revocation_*` below, against
    the real predicate (this test monkeypatches it, so it can never see a
    predicate that always returns True).
    """
    org = _signup_org(client, fake)
    real_claim = sc.claim_api_key_revocation

    def _losing_claim(cp, key_id, now=None):
        real_claim(cp, key_id, now)   # the row IS revoked…
        return False                  # …but this call did not win the claim

    monkeypatch.setattr(sc, "claim_api_key_revocation", _losing_claim)
    r = _rotate(client, org["key_id"], _auth(org["key"]))
    assert r.status_code == 409, r.text
    assert _live(fake, org["org_id"]) == [], (
        "the loser must revoke its own replacement — no orphan live key"
    )


def test_claim_api_key_revocation_is_a_real_conditional_claim(fake):
    """T7 (the predicate ITSELF): `claim_api_key_revocation` must report
    whether THIS call revoked a LIVE row — True exactly once per row, False for
    a row that was already revoked (and it must not re-stamp it).

    This is the guard the whole cap-neutral rotate rests on: an unconditional
    revoke is a claim that always succeeds, and a rotate racing a plain DELETE
    of the same row would then credit a slot it never released. Exercised
    DIRECTLY against the real predicate — nothing here is monkeypatched, so an
    always-True or filter-free claim cannot pass it.
    """
    stamp = datetime.now(UTC).isoformat()
    fake.tables.setdefault("api_keys", []).extend([
        {"id": "key_live_4355", "org_id": "org_4355", "revoked_at": None},
        {"id": "key_dead_4355", "org_id": "org_4355", "revoked_at": stamp},
    ])
    live = _row_by_id(fake, "key_live_4355")
    assert sc.claim_api_key_revocation(fake, "key_live_4355") is True, (
        "a LIVE row must be claimed by this call"
    )
    claimed_stamp = live["revoked_at"]
    assert claimed_stamp is not None, "the winner must have stamped revoked_at"
    # The SAME row, now revoked → the claim LOSES. An unconditional PATCH would
    # answer True here (and re-stamp the tombstone).
    assert sc.claim_api_key_revocation(fake, "key_live_4355") is False, (
        "a row this call did not find live must NOT be claimed"
    )
    assert live["revoked_at"] == claimed_stamp, (
        "a lost claim must leave the winner's tombstone untouched"
    )
    # A row already revoked BEFORE the first call is never claimable either.
    dead = _row_by_id(fake, "key_dead_4355")
    assert sc.claim_api_key_revocation(fake, "key_dead_4355") is False
    assert dead["revoked_at"] == stamp, (
        "an already-revoked row must not be re-stamped"
    )


def test_concurrent_rotate_of_one_row_admits_exactly_one(client, fake, monkeypatch):
    """T7 (the RACE, not a simulation of it): two real clients rotating the SAME
    row simultaneously. Both requests are held at the claim write, so BOTH have
    already proved the row live (`api_key_occupies_slot`) and minted their
    replacements — then the CAS decides. Exactly one 200, one 409, and the live
    count is exactly where it started.

    Why the rendezvous matters for non-vacuity. Without it the two requests can
    serialize, and the loser would exit at the `api_key_occupies_slot` check
    (409) BEFORE reaching the claim — so an unconditional claim would still pass
    the assertion. Holding both at the claim makes the claim the deciding write,
    which is the only interleaving in which the cap-raise is observable.

    The rendezvous wraps the control-plane's `query` (it only SCHEDULES; the
    real PATCH still decides), so the predicate under test is untouched.
    """
    org = _claimed_session_org(client, fake, monkeypatch)
    # The race needs the cap gate to admit BOTH mints: the loser's admission
    # check runs after the winner's insert, so a cap of 2 would 402 the loser
    # before it ever reached the claim. `solo` (max_api_keys=5) keeps the gate
    # out of the way of the claim. Real tier, real caller, no override.
    org_row = next(r for r in fake.tables["organizations"]
                   if r["id"] == org["org_id"])
    org_row["tier"] = "solo"
    target = "key_target_4355"
    fake.tables.setdefault("api_keys", []).append({
        "id": target, "org_id": org["org_id"],
        "lookup_hash": "hash_target_4355", "key_prefix": "tk_target4355",
        "created_via": "provisioned", "created_by": "api",
        "created_at": datetime.now(UTC).isoformat(),
        "revoked_at": None, "expires_at": None, "name": "target",
        "graph_id": None, "scopes": [], "delegation_depth": None,
        "created_by_key_id": None,
    })
    before = len(_live(fake, org["org_id"]))
    assert before == 2, before

    gate = threading.Barrier(2, timeout=60)
    real_query = fake.query

    def _rendezvous_at_claim(table, **kwargs):
        filters = kwargs.get("filters") or []
        # The claim write — keyed on the TARGET id, which the compensation
        # write (the loser's replacement id) can never match. Under the
        # mutation this class is named for (filter reduced to the id), the
        # first filter is unchanged, so the rendezvous still fires.
        if (table == "api_keys" and kwargs.get("method") == "PATCH"
                and filters[:1] == [("id", "eq", target)]):
            gate.wait(timeout=60)
        return real_query(table, **kwargs)

    monkeypatch.setattr(fake, "query", _rendezvous_at_claim)

    results: list = [None, None]
    errors: list[BaseException] = []

    def _worker(i: int) -> None:
        try:
            results[i] = _rotate(client, target, org["headers"])
        except BaseException as e:
            errors.append(e)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not errors, errors
    assert not any(t.is_alive() for t in threads), (
        "a rotate thread never returned — the claim rendezvous deadlocked"
    )
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 409], [
        (r.status_code, r.text) for r in results
    ]
    assert len(_live(fake, org["org_id"])) == before, (
        "a rotate racing a rotate must leave the live count unchanged"
    )
    winner = next(r for r in results if r.status_code == 200)
    assert _row_by_id(fake, target)["revoked_at"] is not None
    assert _row_by_id(fake, winner.json()["id"])["revoked_at"] is None, (
        "the winner's replacement must be the live one"
    )


# ── audit / response shape ──────────────────────────────────────────────────

def test_rotate_emits_the_rotate_audit_event(client, fake, monkeypatch):
    """A rotate must not be invisible to the audit trail: `api_key_rotate`
    names the new row and the row it displaced."""
    org = _signup_org(client, fake)
    events: list[dict] = []

    async def _fake_audit(request, org_id, operation, **kwargs):
        events.append({"org_id": org_id, "operation": operation, **kwargs})

    monkeypatch.setattr(ha_mod, "_async_audit", _fake_audit)
    r = _rotate(client, org["key_id"], _auth(org["key"]))
    assert r.status_code == 200, r.text
    assert len(events) == 1, events
    ev = events[0]
    assert ev["operation"] == "api_key_rotate"
    assert ev["org_id"] == org["org_id"]
    assert ev["resource_id"] == r.json()["id"]
    assert ev["detail"] == {"replaced_key_id": org["key_id"],
                            "replaced_revoked": True}


def test_rotate_stores_only_the_hash_and_reveals_once(client, fake):
    """Reveal-once: the response carries the new plaintext exactly once, and
    only its hash is stored."""
    from tortoise.auth import lookup_hash

    org = _signup_org(client, fake)
    r = _rotate(client, org["key_id"], _auth(org["key"]))
    assert r.status_code == 200, r.text
    new_key = r.json()["key"]
    row = next(x for x in _rows(fake, org["org_id"]) if x["id"] == r.json()["id"])
    assert row["lookup_hash"] == lookup_hash(new_key)
    assert new_key not in str(row)
