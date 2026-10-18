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

Supabase lane (FakeControlPlane) is the default for this file. The registry
lane is covered here too, but only where a defect is LANE-SPECIFIC: the
concurrent-claim race (T7) has two implementations — the Supabase PATCH and the
Cypher twin in `hosted_api._claim_key_revocation` — so each is raced against its
own store (#4721). Everything else is asserted on the Supabase lane, and the
hosted E2E suite (`tests/e2e/hosted/test_02_free_tier_limits.py`) covers the
registry lane end-to-end. Session-authentication is used where a test needs
more than one live credential (the anon org's derived tier caps `max_api_keys`
at 1) or needs to exercise the owner/admin session gates.
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
from tests._verdict import verdict_client
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
    with verdict_client(app) as c:
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


def _reraise_captured_skip(errors: list[BaseException]) -> None:
    """Re-raise a `pytest.skip` that a RACE THREAD captured, in the main thread.

    pytest honours a skip only when it is raised in the TEST's own frame. The
    verdict seam (`tests/_verdict.py::verdict_client`) raises the #5049
    INCONCLUSIVE skip at the point of the request, and in a threaded race that
    point is inside the worker — so `except BaseException` captures it into
    `errors` and a bare `assert not errors` then flattens a NON-ANSWER into a
    hard FAIL. That is rule 1 of #5049 (`load must skip INCONCLUSIVE, never
    red`) failing from the seam adopted to prevent it: a loaded box would
    manufacture a red out of machine load.

    A captured skip NEVER downgrades a genuine failure (#4721 review P2): the
    list is PARTITIONED, so the skip is re-raised only when EVERY captured
    exception is a skip. The earlier shape raised the first skip it found and
    returned through the `raise`, silently discarding a real exception
    captured alongside it — a red reported as INCONCLUSIVE, i.e. the #5049
    rule broken in the opposite direction (fail-open). Genuine failures win:
    with `errors == [<failure>, <skip>]` the failure is what pytest records.

    Call this AFTER the deadlock (liveness) assertion and BEFORE
    `assert not errors`. The liveness fact is independent of the skip
    contract — a worker still parked in the rendezvous is a real failure and
    must not be reported as a skip.
    """
    skips = [e for e in errors if isinstance(e, pytest.skip.Exception)]
    others = [e for e in errors if not isinstance(e, pytest.skip.Exception)]
    if others:
        raise others[0]
    if skips:
        raise skips[0]


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


def test_rotate_can_lengthen_the_displaced_rows_expiry(client, fake, monkeypatch):
    """T6 (P1, review round 1): the replacement inherits the target's privilege
    CLASS (graph, scopes, delegation, lineage) but NOT its lifetime. The body's
    expiry is parsed BEFORE the displaced row is read (`hosted_api.py:11365`)
    and inherited only when the body omitted one (`:11413-11414`), so a rotate
    CAN extend a credential's life by up to 366 days.

    This is the widening path the brief documents in §2.3 item 1 and the
    constraint #2446's member replace must add (`replacement expiry <= displaced
    expiry`). The doc previously claimed the primitive could not widen expiry at
    all — this test is that claim's non-vacuity proof: it passes on the shipped
    primitive, so the old claim was false.
    """
    org = _claimed_session_org(client, fake, monkeypatch)
    target = _mint(client, org["headers"], {"name": "short", "expires_in": 1})
    r = _rotate(client, target["id"], org["headers"], {"expires_in": 366})
    assert r.status_code == 200, r.text
    displaced = datetime.fromisoformat(target["expires_at"])
    replacement = datetime.fromisoformat(r.json()["expires_at"])
    assert replacement > displaced, (
        "a body-supplied expiry must be able to LENGTHEN the replacement — "
        "rotate bounds the privilege class, not the lifetime"
    )
    assert (replacement - datetime.now(UTC)).days >= 365


# ── T3: never leave the caller with no key ──────────────────────────────────

def test_create_leg_failure_leaves_the_old_key_live(client, fake, monkeypatch):
    """T3: if the replacement cannot be created, NOTHING may be revoked."""
    org = _signup_org(client, fake)

    def _boom(_cp, _row, **_kw):
        raise RuntimeError("Supabase unreachable (simulated)")

    # #1879: the Supabase insert now runs inside the `provision_api_key` RPC
    # (one transaction with the cap gate), so THIS is the create-leg seam.
    monkeypatch.setattr(sc, "mint_provisioned_key", _boom)
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
    # The liveness fact comes FIRST: a worker still parked in the rendezvous is
    # a real failure, and a captured skip must not report it as INCONCLUSIVE.
    assert not any(t.is_alive() for t in threads), (
        "a rotate thread never returned — the claim rendezvous deadlocked"
    )
    # #5049: a load refusal reaches this race as a skip raised INSIDE the
    # worker (verdict_client), so it must be re-raised here to count as a skip
    # rather than be asserted into a red below — and only when it is the ONLY
    # kind of thing captured (see `_reraise_captured_skip`).
    _reraise_captured_skip(errors)
    assert not errors, errors
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


def test_the_client_seam_turns_a_wait_bound_refusal_into_an_inconclusive_skip(
        monkeypatch):
    """#8818: the deterministic refusal pin for this module's client seam.

    No load and no wall-clock wait: the bound is forced to 0 s, so the refusal
    comes from the REAL ``WaitBoundMiddleware``. ``POST /v1/team/keys`` is the
    route this file reads most and it is not exempt from the bound. The
    plain-client control is the non-vacuity leg — it is the exact
    ``assert 504 == 200`` shape this conversion removes, and it goes RED if
    the route is ever exempted (the middleware stops refusing, so the
    control's 504 assertion fails).
    """
    from tortoise import mcp_auth
    from tortoise.hosted_api import _TRANSPORT_WAIT_BOUND_EXEMPT

    route = ("POST", "/v1/team/keys")
    assert route not in _TRANSPORT_WAIT_BOUND_EXEMPT
    monkeypatch.setattr(mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 0.0)
    with TestClient(app) as raw:
        assert raw.post(route[1], json={}).status_code == 504
    with verdict_client(app) as tc, pytest.raises(pytest.skip.Exception) as excinfo:
        tc.post(route[1], json={})
    message = str(excinfo.value)
    assert "INCONCLUSIVE [#5049]" in message
    assert "POST /v1/team/keys answered" in message


def test_a_threaded_non_answer_is_re_raised_as_the_contracts_skip(monkeypatch):
    """#5049 across a THREAD boundary — the exchange these races depend on.

    ``verdict_client`` raises the INCONCLUSIVE skip at the point of the
    REQUEST (``tests/_verdict.py::_answered``); in a threaded race that point
    is inside the worker. pytest honours a skip only in the test's own frame,
    so a worker's skip captured by ``except BaseException`` and then asserted
    with a bare ``assert not errors`` becomes a RED — a NON-ANSWER reported as
    a failure, i.e. #5049 rule 1 broken by the very seam adopted to obey it.

    Non-vacuity in both directions: the pre-fix shape (``assert not errors``)
    fails on the captured skip below, and the fix re-raises it as the
    contract's skip. The bound is forced to 0 s, so the refusal comes from the
    real ``WaitBoundMiddleware`` — no load and no wall-clock wait.
    """
    from tortoise import mcp_auth

    monkeypatch.setattr(mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 0.0)
    errors: list[BaseException] = []
    box: list = []

    def _worker(tc) -> None:
        try:
            box.append(tc.post("/v1/team/keys", json={}))
        except BaseException as e:  # mirrors the race workers above
            errors.append(e)

    with verdict_client(app) as tc:
        t = threading.Thread(target=_worker, args=(tc,))
        t.start()
        t.join(timeout=60)
    assert not t.is_alive(), "the worker never returned from its refused request"
    assert not box, "the refusal must not produce a response"
    assert errors and isinstance(errors[0], pytest.skip.Exception), errors
    # The pre-fix shape: the skip is flattened into a hard FAIL.
    with pytest.raises(AssertionError):
        assert not errors, errors
    # The fix: re-raised in the main thread, where pytest records the skip.
    with pytest.raises(pytest.skip.Exception):
        _reraise_captured_skip(errors)


def test_a_captured_skip_never_downgrades_a_genuine_failure(monkeypatch):
    """The other direction of the #5049 seam: failures win, skips do not mask.

    A loaded box can refuse ONE worker's request (captured skip) while its
    partner hits a real fault — a 500 through TestClient's
    ``raise_server_exceptions``, an embedded-DB error, or the winner's own
    path. The pre-fix shape raised the FIRST skip it found and returned
    through the `raise`, so a genuine exception sitting next to it was
    discarded and the run was recorded as SKIP: a real failure reported as
    INCONCLUSIVE (fail-open), which is the #5049 contract broken in the
    opposite direction.

    Non-vacuity: with ``errors == [AssertionError, Skipped]`` the pre-fix
    helper raises the skip (the test below fails on that), the fix raises the
    AssertionError. Built from a REAL captured skip (the forced-0s bound) so
    the element under test is the one the race workers actually produce.
    """
    from tortoise import mcp_auth

    monkeypatch.setattr(mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 0.0)
    with verdict_client(app) as tc, pytest.raises(pytest.skip.Exception) as excinfo:
        tc.post("/v1/team/keys", json={})
    captured_skip = excinfo.value
    assert isinstance(captured_skip, pytest.skip.Exception)

    failure = AssertionError("the other worker hit a real fault")
    # The order that defeats the pre-fix shape: the genuine failure is FIRST,
    # so a `for` loop raising the first skip it meets walks past it. The call's
    # escape is captured rather than asserted with `pytest.raises` — under the
    # pre-fix helper it raises the SKIP, which pytest would honour and record
    # as a skip (the masking this case exists to catch); captured, it is a plain
    # red on the assertion below.
    mixed: list[BaseException] = [failure, captured_skip]
    raised_exc: BaseException | None = None
    try:
        _reraise_captured_skip(mixed)
    except BaseException as e:  # noqa: BLE001 — the raised TYPE is the assertion
        raised_exc = e
    assert raised_exc is failure, (
        "a captured skip must never downgrade a genuine failure — the FAILURE "
        f"is what pytest records; got {raised_exc!r}"
    )
    # And a skip-only race still skips: the conversion this seam exists for.
    with pytest.raises(pytest.skip.Exception) as skipped:
        _reraise_captured_skip([captured_skip])
    assert skipped.value is captured_skip
    # Nothing captured at all: nothing raised (the ordinary green race).
    assert _reraise_captured_skip([]) is None


# ── T7, registry lane: the CYPHER twin's claim, raced ───────────────────────
# `test_concurrent_rotate_of_one_row_admits_exactly_one` above races the
# Supabase claim (`claim_api_key_revocation`, a conditional PATCH). The
# registry/selfhost lane uses the Cypher twin in
# `hosted_api._claim_key_revocation` — `MATCH … WHERE k.revoked_at IS NULL SET
# … RETURN k.id` — and NOTHING raced it: this file is Supabase-only (the
# autouse `_env` fixture above) and the hosted E2E covers cap-neutrality and
# the non-occupying refusal but not the concurrent claim. "Believed correct"
# is not the standard a rotate claim is held to (#4721).

_REG_SDKS: list = []


@pytest.fixture
def reg_client(_env, monkeypatch):
    """A hosted REST client on the REGISTRY lane (one temp DB, its own graphs).

    `_env` is requested explicitly, not assumed: it is autouse and pins
    `TORTOISE_CONTROL_PLANE=supabase` at setup, so this fixture runs AFTER it
    and undoes exactly that (the fixture ordering, not luck, is what makes the
    lane switch deterministic). `patched_tortoise_sdk` binds every no-path SDK
    construction to one temp DB (the #1497/#1950 anchor rules).

    Lane scope, measured rather than assumed (#4721 review): under a
    `TORTOISE_DB_URI` lane the #1647 redirect fires here (the temp path is
    passed POSITIONALLY, so `explicit_path` is true and this stem is not in
    `TEST_NO_REDIRECT_STEMS`), so both SDKs land on the SERVER — but not in a
    shared graph: the redirect renames it per run to `test_<pathstem>_<hash>`
    and `_get_registry` derives `test_registry_<...>_control_plane` from that,
    both `test_`-prefixed and journaled for the session sweep. Two consecutive
    docker-lane runs of the cases below are green. The assertions still do not
    rely on it: the seeded ids carry a per-run suffix, so a row left by ANY
    other run can never enter this test's live set.

    The client is `verdict_client`, matching this module's `client` fixture. A
    request parked past `_TRANSPORT_WAIT_BOUND_S` under machine load is a
    NON-ANSWER, and the exact `sorted(statuses) == [200, 409]` assertion below
    cannot tell a 504 refusal from a real verdict — so a raw `TestClient` would
    turn load into a red. The seam converts that refusal into the contract's
    INCONCLUSIVE skip (#5049) **provided the test re-raises it out of the
    worker thread**, which pytest cannot do on its own: the requests here run
    in threads, so the cases below call `_reraise_captured_skip(errors)` before
    asserting on `errors` (see
    `test_a_threaded_non_answer_is_re_raised_as_the_contracts_skip`).
    """
    import tempfile

    from tests._http_fixtures import patched_tortoise_sdk

    for var in ("TORTOISE_CONTROL_PLANE", "SUPABASE_URL",
                "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SERVICE_KEY"):
        monkeypatch.delenv(var, raising=False)
    with (
        tempfile.TemporaryDirectory() as tmpdir,
        patched_tortoise_sdk(os.path.join(tmpdir, "rotate-registry.db")),
    ):
        try:
            with verdict_client(app) as tc:
                yield tc
        finally:
            while _REG_SDKS:
                try:  # noqa: SIM105
                    _REG_SDKS.pop().close()
                except Exception:
                    pass


@pytest.fixture
def reg():
    """The registry graph handle (kept alive past the test — #1588)."""
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK(namespace="registry")
    _REG_SDKS.append(sdk)
    return sdk._get_registry()


def _reg_seed_org(reg, org_id: str, *, max_api_keys: int = 5) -> None:
    """One Team with headroom above the cap gate.

    The cap must NOT decide this race: the loser's admission check runs after
    the winner's insert, so at a cap of 2 it would 402 before ever reaching the
    claim — and then an UNCONDITIONAL claim would still pass the assertion. 5
    keeps the gate out of the way so the CLAIM is the deciding write (the same
    reason the Supabase twin uses the `solo` tier).
    """
    reg.query(
        "CREATE (t:Team {id:$id, name:$id, tier:'free', max_api_keys:$mak})",
        params={"id": org_id, "mak": max_api_keys},
    )


def _reg_seed_key(reg, org_id: str, key_id: str, *, token: str | None = None,
                  created_via: str = "provisioned",
                  revoked_at: str | None = None) -> None:
    """A registry APIKey node. `token` makes it AUTHENTICABLE (real
    salt:hash + its 10-char prefix, the shape `get_current_org` scans for); no
    `scopes`/`delegation_depth` props → `legacy_full_access`, i.e. the owner
    class `_require_keys_manage` admits."""
    from tortoise.auth import hash_api_key

    reg.query(
        "CREATE (k:APIKey {id:$id, org_id:$tid, key_hash:$kh, key_prefix:$kp, "
        "created_at:$ca, revoked_at:$ra, expires_at:null, created_via:$cv})",
        params={
            "id": key_id, "tid": org_id,
            "kh": hash_api_key(token) if token else "salt:unused",
            "kp": token[:10] if token else "tt_unseen_",
            "ca": datetime.now(UTC).isoformat(),
            "ra": revoked_at, "cv": created_via,
        },
    )


def _reg_live(reg, org_id: str) -> list[str]:
    rows = reg.query(
        "MATCH (k:APIKey {org_id:$tid}) WHERE k.revoked_at IS NULL RETURN k.id",
        params={"tid": org_id},
    ).result_set
    return sorted(r[0] for r in rows)


def _reg_revoked(reg, key_id: str):
    rows = reg.query(
        "MATCH (k:APIKey {id:$id}) RETURN k.revoked_at", params={"id": key_id},
    ).result_set
    return rows[0][0] if rows else None


def test_registry_lane_concurrent_rotate_of_one_row_admits_exactly_one(
        reg_client, reg):
    """T7 on the CYPHER twin: two real clients rotating the SAME registry node
    simultaneously. Both are held at the claim write, so both have already
    proved the row live and minted their replacements — then the conditional
    `SET … WHERE k.revoked_at IS NULL` decides. Exactly one 200, one 409, and
    the live count is where it started.

    Non-vacuity: the rendezvous is what makes the claim the deciding write. On
    the registry lane the claim is a single `graph.query`, so the barrier wraps
    that call; dropping the `WHERE k.revoked_at IS NULL` guard (an
    unconditional SET returning the row) makes BOTH calls claim and this
    assertion fails with two 200s.
    """
    # Per-RUN ids, not fixed ones: the live-set equality below must be a
    # statement about THIS run's rows only. Nothing in this test may depend on
    # the graph having been empty when it started (a fixed id would make the
    # assertion an equality against whatever a previous run left behind).
    suffix = uuid.uuid4().hex[:8]
    org_id = f"team-4721r-{suffix}"
    target = f"key_target_4721-{suffix}"
    caller_key = f"key_caller_4721-{suffix}"
    caller_token = f"tt_caller_{suffix}_signin"
    _reg_seed_org(reg, org_id)
    _reg_seed_key(reg, org_id, caller_key, token=caller_token)
    _reg_seed_key(reg, org_id, target)

    before = _reg_live(reg, org_id)
    assert before == [caller_key, target], before

    gate = threading.Barrier(2, timeout=60)
    # Both threads must actually ARRIVE at the claim: if the barrier timed out
    # (or only one thread ever ran the claim), the broken wait raises into the
    # caller's `except Exception` and produces a 409 that looks exactly like a
    # lost claim — a false pass. Counting the waiters makes the race itself an
    # assertion, not an assumption.
    claim_waiters: list[int] = []
    from tortoise.sdk import TortoiseSDK as _SDK

    real_get_registry = _SDK._get_registry
    claim_marker = "SET k.revoked_at = $now RETURN k.id"

    class _RendezvousAtClaim:
        """Delegating proxy: waits for BOTH threads on the CLAIM statement
        only, then performs the real write. Only SCHEDULING is wrapped — the
        predicate under test is untouched."""

        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def query(self, cypher, *args, **kwargs):
            if claim_marker in cypher:
                claim_waiters.append(1)
                gate.wait(timeout=60)
            return self._inner.query(cypher, *args, **kwargs)

    def _patched_get_registry(self, *a, **kw):
        return _RendezvousAtClaim(real_get_registry(self, *a, **kw))

    from unittest import mock

    results: list = [None, None]
    errors: list = []

    def _worker(i: int) -> None:
        try:
            results[i] = _rotate(reg_client, target, _auth(caller_token))
        except BaseException as e:
            errors.append(e)

    with mock.patch.object(_SDK, "_get_registry", _patched_get_registry):
        threads = [threading.Thread(target=_worker, args=(i,)) for i in (0, 1)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
    # Same order as the Supabase twin: liveness first (a deadlock is a real
    # failure), then the #5049 re-raise, then the errors assertion.
    assert not any(t.is_alive() for t in threads), (
        "a rotate thread never returned — the claim rendezvous deadlocked"
    )
    _reraise_captured_skip(errors)
    assert not errors, errors
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 409], [(r.status_code, r.text) for r in results]
    assert len(claim_waiters) == 2, (
        "both rotates must reach the CLAIM write — otherwise the two requests "
        "serialized and the claim was never the deciding write"
    )

    # The claim, not the cap gate, decided: the loser reached the claim (it
    # minted a replacement and compensated it) rather than 402ing earlier.
    loser = next(r for r in results if r.status_code == 409)
    assert "rolled back" in loser.json()["detail"], (
        "the loser must be the CLAIM refusal (replacement rolled back), not the "
        f"non-occupying refusal: {loser.json()['detail']}"
    )

    winner = next(r for r in results if r.status_code == 200)
    after = _reg_live(reg, org_id)
    assert len(after) == len(before), (
        f"a rotate racing a rotate must leave the live count unchanged: {after}"
    )
    assert target not in after, "the winner's claim revoked the displaced row"
    assert winner.json()["id"] in after, (
        "the winner's replacement is the live row that replaced it"
    )
    assert winner.json()["replaced_revoked"] is True
    assert _reg_revoked(reg, target) is not None, (
        "the winner's claim must have revoked the displaced row"
    )
    assert _reg_revoked(reg, winner.json()["id"]) is None, (
        "the winner's replacement must be the live one"
    )


def test_registry_lane_claim_refuses_a_row_already_revoked(reg_client, reg):
    """The registry twin's other half: an unconditional SET would still pass
    the race above whenever the two calls serialize, so pin the CONDITION — a
    second claim on an already-revoked row reports False (nothing matched) and
    does not overwrite the winner's `revoked_at`.

    This pins the CLAIM primitive only (the boolean IS the claim). The
    endpoint-level consequence — the rotate refuses 409 and compensates its
    replacement, leaving no orphan live key — is pinned by the race case
    above, not here."""
    from tortoise.hosted_api import _claim_key_revocation

    suffix = uuid.uuid4().hex[:8]  # per-run rows — see the race case above
    org_id = f"team-4721s-{suffix}"
    target = f"key_target_4721s-{suffix}"
    prior = datetime.now(UTC).isoformat()
    _reg_seed_org(reg, org_id)
    _reg_seed_key(reg, org_id, target, revoked_at=prior)

    now = datetime.now(UTC).isoformat()
    assert _claim_key_revocation(org_id, target, now) is False, (
        "a claim on a revoked row must report NO match — that boolean IS the "
        "claim"
    )
    assert _reg_revoked(reg, target) == prior, (
        "a losing claim must not overwrite the winner's revoked_at"
    )
