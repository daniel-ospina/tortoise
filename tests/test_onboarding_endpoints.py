"""Tests for hosted onboarding endpoints (#498).

Covers: self-service register, public demo graph, onboarding state
(GET/PATCH), session-recording toggle, hosted team creation.

Uses embedded FalkorDBLite + registry SDK (mirrors test_hosted_api.py).
"""
from __future__ import annotations

import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import pytest  # noqa: I001
from fastapi.testclient import TestClient

from tortoise.hosted_api import app, _make_sdk
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def client(tmp_path):
    """TestClient with a temp embedded DB + registry."""
    fixture_db_path = str(tmp_path / "onboarding.db")
    # Patch TortoiseSDK to use the temp DB (mirrors test_hosted_api.py)
    orig_init = TortoiseSDK.__init__

    def _patched(self, db_path_arg=None, *, namespace=None, **kw):
        # Isolate EVERY SDK construction (registry included) to the fixture's
        # temp DB. The fixture path is FORCED unconditionally (caller kwargs
        # dropped) — _make_sdk's embedded-lane fallback constructs
        # TortoiseSDK(db_path=<shared /tmp/tortoise.db>) and forwarding that
        # caller path binds the SHARED fallback DB, leaking team state across
        # tests ("Sub-team already created" 409s in the URI-less tier-2 CI
        # lane; URI-mode constructions pass no db_path and already fall to
        # the fixture path). Same pattern as
        # test_hosted_api._patch_tortoise_sdk_init. NOTE: the closure var
        # must NOT be named `db_path` — a `db_path` parameter would shadow
        # it, silently re-enabling the leak.
        orig_init(self, db_path=fixture_db_path, namespace=namespace)

    TortoiseSDK.__init__ = _patched
    # #1497: break the _make_sdk embedded fallback anchor — module-level
    # _FALLBACK_KEEPALIVE survives tests, so an anchored SDK bound to a prior
    # test's temp DB leaks state / dies socket. Re-bind to THIS temp DB.
    from tortoise.hosted_api import _FALLBACK_KEEPALIVE
    _FALLBACK_KEEPALIVE.clear()
    from tortoise.hosted_api import get_current_team
    app.dependency_overrides[get_current_team] = lambda: {
        "team_id": "test-team-1", "tier": "free", "key_id": "k1",
        "max_users": 1, "max_graphs": 1, "max_teams": 1,
        # #1748: the onboarding sub-team is provisioned on the USER path —
        # the session user becomes the owner member (get_current_team_session
        # attaches session_user_id for session JWT auth; tests seed it here).
        "session_user_id": "user-1",
    }
    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.clear()
    TortoiseSDK.__init__ = orig_init
    # #1502-class teardown parity (mirror test_hosted_api._restore_...):
    # evict the anchor created during this test so a stale SDK bound to this
    # test's temp DB never leaks into the next test file's run.
    from tortoise.hosted_api import _FALLBACK_KEEPALIVE
    _FALLBACK_KEEPALIVE.clear()


@pytest.fixture
def unauth_client(tmp_path):
    """TestClient WITHOUT the auth override — real 401s."""
    fixture_db_path = str(tmp_path / "unauth.db")
    orig_init = TortoiseSDK.__init__

    def _patched(self, db_path_arg=None, *, namespace=None, **kw):
        # Isolate EVERY SDK construction (registry included) to the fixture's
        # temp DB (see the client fixture — the closure var is
        # `fixture_db_path` so a `db_path` parameter could never shadow it).
        orig_init(self, db_path=fixture_db_path, namespace=namespace)

    TortoiseSDK.__init__ = _patched
    from tortoise.hosted_api import _FALLBACK_KEEPALIVE
    _FALLBACK_KEEPALIVE.clear()
    with TestClient(app) as tc:
        yield tc
    TortoiseSDK.__init__ = orig_init
    from tortoise.hosted_api import _FALLBACK_KEEPALIVE
    _FALLBACK_KEEPALIVE.clear()


# ── Onboarding state ────────────────────────────────────────────

class TestOnboardingState:
    def test_get_state_requires_auth(self, unauth_client):
        r = unauth_client.get("/v1/onboarding/state")
        assert r.status_code == 401

    def test_get_state_default(self, client):
        r = client.get("/v1/onboarding/state")
        assert r.status_code == 200
        body = r.json()
        assert "onboarding" in body

    def test_patch_state_merge(self, client):
        r = client.patch("/v1/onboarding/state", json={"demo_created": True})
        assert r.status_code == 200
        body = r.json()
        assert body["onboarding"]["demo_created"] is True

    def test_patch_state_invalid_key(self, client):
        r = client.patch("/v1/onboarding/state", json={"not_a_field": 1})
        # Unknown keys either rejected (400) or ignored — but never 500
        assert r.status_code < 500


# ── Public demo graph ───────────────────────────────────────────

class TestPublicDemo:
    def test_demo_requires_auth(self, unauth_client):
        r = unauth_client.post("/v1/demo")
        assert r.status_code == 401

    def test_demo_creates_points(self, client):
        r = client.post("/v1/demo")
        assert r.status_code == 200
        body = r.json()
        assert "points_created" in body or "created" in body

    def test_demo_idempotent(self, client):
        r1 = client.post("/v1/demo")
        r2 = client.post("/v1/demo")
        assert r1.status_code == 200
        assert r2.status_code == 200  # no crash on re-run


# ── Session recording toggle ────────────────────────────────────

class TestSessionRecording:
    def test_enable_recording(self, client):
        r = client.post("/v1/onboarding/session-recording", json={"enabled": True})
        assert r.status_code == 200
        assert r.json()["onboarding"]["session_recording"] is True

    def test_disable_recording(self, client):
        r = client.post("/v1/onboarding/session-recording", json={"enabled": False})
        assert r.status_code == 200
        assert r.json()["onboarding"]["session_recording"] is False

    def test_enable_writes_capture_revised(self, client):
        """#1927: the session-recording endpoint writes the OFF-SWITCH flag
        (default ON, ToS-covered) + ``capture_revised`` (kept for
        backward-compat with the registered state keys — the re-ask
        machinery it fed was removed)."""
        r = client.post("/v1/onboarding/session-recording", json={"enabled": True})
        assert r.status_code == 200, r.text
        st = r.json()["onboarding"]
        assert st["session_recording"] is True
        assert st["capture_revised"] is True

    def test_disable_writes_capture_revised(self, client):
        """#1927: toggle-off (the quiet off-switch) writes the flag False
        + ``capture_revised`` (backward-compat write)."""
        r = client.post("/v1/onboarding/session-recording", json={"enabled": False})
        assert r.status_code == 200, r.text
        st = r.json()["onboarding"]
        assert st["session_recording"] is False
        assert st["capture_revised"] is True


# ── #1728 Slice 3 (Task 18): Q3 prompt ↔ wizard single consent source ─────
# The AGENT_ONBOARDING.md Q3 yes/no branches write the SAME consent keys as
# the wizard's Memory-sources sessions toggle (one consent source — no
# cross-surface divergence). The Q3 executor is the MCP tool
# tortoise_onboarding_session_recording; the wizard rides the PATCH surface.
# Both must produce the identical state shape, and a stdio user who declined
# must be able to re-enable via the tool REGARDLESS of ``capture_revised``.


def _invoke_session_recording_tool(tmp_path, team_id: str, enabled: bool):
    """Invoke the MCP tool the way Q3 executes it (HTTP mode team context)
    against an isolated temp SDK, returning (result, state_after)."""
    from tortoise.mcp_auth import _current_team_id
    from tortoise.mcp_server import tortoise_onboarding_session_recording
    orig_init = TortoiseSDK.__init__

    def _patched(self, db_path_arg=None, *, namespace=None, db_path=None, **kw):
        orig_init(self, db_path=db_path if db_path_arg is None else db_path_arg,
                  namespace=namespace, **kw)

    TortoiseSDK.__init__ = _patched
    from tortoise.hosted_api import _FALLBACK_KEEPALIVE
    _FALLBACK_KEEPALIVE.clear()
    from tortoise.hosted_api import _get_onboarding_state, _make_sdk
    _make_sdk(namespace="registry")._get_registry().query(
        "CREATE (t:Team {id:$id, onboarding_state:$st})",
        params={"id": team_id, "st": "{}"},
    )
    tok = _current_team_id.set(team_id)
    try:
        result = tortoise_onboarding_session_recording(enabled=enabled)
        state = _get_onboarding_state(team_id)
    finally:
        _current_team_id.reset(tok)
        _FALLBACK_KEEPALIVE.clear()
        TortoiseSDK.__init__ = orig_init
    return result, state


def test_q3_and_wizard_write_same_keys(tmp_path):
    """#1728 (UX P1-b): Q3's yes-branch writes the SAME keys as the wizard's
    sessions toggle-on — the enforced ``session_recording`` flag (the data-
    plane consent) + ``capture_revised`` (re-ask resolution)."""
    result, state = _invoke_session_recording_tool(tmp_path, "team-1728-q3", True)
    assert "error" not in result, result
    assert state["session_recording"] is True
    assert state["capture_revised"] is True
    # The wizard's sessions toggle-on PATCH produces the identical state
    # shape (PATCH merge with the same two keys — the single consent source).
    from tortoise.hosted_api import app as _app
    from tortoise.hosted_api import get_current_team
    orig_init = TortoiseSDK.__init__

    def _patched(self, db_path_arg=None, *, namespace=None, db_path=None, **kw):
        orig_init(self, db_path=db_path if db_path_arg is None else db_path_arg,
                  namespace=namespace, **kw)

    TortoiseSDK.__init__ = _patched
    _app.dependency_overrides[get_current_team] = lambda: {
        "team_id": "team-1728-q3", "tier": "free", "key_id": "k1",
    }
    try:
        with TestClient(_app) as tc:
            r = tc.patch("/v1/onboarding/state",
                         json={"session_recording": True, "capture_revised": True})
            assert r.status_code == 200, r.text
            st = r.json()["onboarding"]
    finally:
        _app.dependency_overrides.clear()
        TortoiseSDK.__init__ = orig_init
    assert st["session_recording"] is True
    assert st["capture_revised"] is True
    # identical state shape — the wizard PATCH and the Q3 tool write the
    # same two consent keys (single consent source)
    assert st["session_recording"] == state["session_recording"]
    assert st["capture_revised"] == state["capture_revised"]


def test_q3_decline_then_reenable_consents(tmp_path):
    """#1728 (cycle-4 P1-B): a stdio/self-hosted user who DECLINED (Q3 no /
    re-ask NO — consent cleared + capture_revised set) can re-enable via
    ``tortoise_onboarding_session_recording(enable=true)`` REGARDLESS of
    ``capture_revised`` — the tool always re-sets the enforced consent flag
    (a user-initiated enable never skips the write)."""
    # decline first (Q3 no writes the same keys as the wizard/panel decline)
    _, state = _invoke_session_recording_tool(tmp_path, "team-1728-re", False)
    assert state["session_recording"] is False
    assert state["capture_revised"] is True
    # decline writes the off-switch flag (capture stops with a 409)
    from tortoise.hosted_api import _get_onboarding_state
    assert _get_onboarding_state("team-1728-re")["session_recording"] is False
    # re-enable via the tool — the flag re-sets despite capture_revised=True
    result, state2 = _invoke_session_recording_tool(tmp_path, "team-1728-re", True)
    assert "error" not in result, result
    assert state2["session_recording"] is True
    assert state2["capture_revised"] is True


def test_fresh_team_defaults_to_recording_on(client):
    """#1927: a FRESH team (no stored flag) reads session_recording=True
    from the default merge — capture works out of the box, no consent gate.
    (The carve-out lane's embedded DB is shared across tests, so the team id
    is unique per run to prove the default rather than inherited state.)"""
    import uuid

    from tortoise.hosted_api import _get_onboarding_state, _make_sdk
    team_id = f"test-team-1927-default-{uuid.uuid4().hex[:8]}"
    _make_sdk(namespace="registry")._get_registry().query(
        "CREATE (t:Team {id:$id, onboarding_state:$st})",
        params={"id": team_id, "st": "{}"},
    )
    st = _get_onboarding_state(team_id)
    assert st["session_recording"] is True
    assert st["capture_revised"] is False


def test_off_switch_patch_stops_capture_409(client, monkeypatch):
    """#1927: disabling session_recording via the PATCH surface stops
    ingestion — a capture POST returns the clear 409 (not the old 403),
    writes NO Session node and NO receipt, and re-enabling restores capture.
    (The carve-out lane's embedded DB is shared across tests, so the test
    resets its own state shape first, and the registry state writer is a
    MATCH...SET — the Team node must exist.)"""
    from tortoise.hosted_api import _make_sdk
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    _make_sdk(namespace="registry")._get_registry().query(
        "CREATE (t:Team {id:$id, onboarding_state:$st})",
        params={"id": "test-team-1", "st": "{}"},
    )
    # reset to a known on state, then capture works
    r0 = client.patch("/v1/onboarding/state", json={"session_recording": True})
    assert r0.status_code == 200, r0.text
    conv = [{"role": "user", "content": "we decided to ship the memory capture slice"},
            {"role": "assistant", "content": "agreed — capture is default-on now"}]
    r1 = client.post("/v1/sessions", json={"conversation": conv})
    assert r1.status_code == 200, r1.text
    sessions_after_on = _make_sdk(namespace="test-team-1")._get_proj().g.query(
        "MATCH (s:Session) RETURN count(s)").result_set[0][0]
    receipts_after_on = {
        k: v for k, v in client.get("/v1/onboarding/state")
        .json()["onboarding"].items()
        if k.startswith("session_capture_receipt") and v
    }
    # off-switch: capture stops with a clear 409
    r2 = client.patch("/v1/onboarding/state", json={"session_recording": False})
    assert r2.status_code == 200, r2.text
    r3 = client.post("/v1/sessions", json={"conversation": conv})
    assert r3.status_code == 409, r3.text
    assert "disabled" in r3.json()["detail"]
    # negative side-effects: 409 must NOT write a NEW Session node or a receipt
    st = r3.json()
    assert "session_id" not in st
    g = _make_sdk(namespace="test-team-1")._get_proj().g
    rows = g.query("MATCH (s:Session) RETURN count(s)").result_set
    assert int(rows[0][0]) == int(sessions_after_on), \
        "409 must not write a Session node"
    st2 = client.get("/v1/onboarding/state").json()["onboarding"]
    receipts_after_409 = {k: v for k, v in st2.items()
                          if k.startswith("session_capture_receipt") and v}
    assert receipts_after_409 == receipts_after_on, \
        "409 must not record a new receipt"
    # re-enable: capture works again
    r4 = client.patch("/v1/onboarding/state", json={"session_recording": True})
    assert r4.status_code == 200, r4.text
    r5 = client.post("/v1/sessions", json={"conversation": conv})
    assert r5.status_code == 200, r5.text


def test_patch_off_switch_keeps_receipts(client):
    """#1927 (T1-P8 + T2-P2e): the off-switch PATCH (toggle-off) clears the
    session_recording flag + sets capture_revised, but NEVER clears probes
    or receipts — re-enable resolves receipt-authoritative."""
    from tortoise.hosted_api import _make_sdk, _update_onboarding_state
    _make_sdk(namespace="registry")._get_registry().query(
        "CREATE (t:Team {id:$id, onboarding_state:$st})",
        params={"id": "test-team-1", "st": "{}"},
    )
    _update_onboarding_state(
        "test-team-1",
        session_recording=True,
        install_probe_claude="2026-08-25T00:00:00Z",
        session_capture_receipt_claude="2026-08-25T00:00:00Z",
    )
    r = client.patch("/v1/onboarding/state", json={
        "session_recording": False, "capture_revised": True,
        "capture_ask_shown": True,
    })
    assert r.status_code == 200, r.text
    st = r.json()["onboarding"]
    assert st["session_recording"] is False
    assert st["capture_revised"] is True
    assert st["install_probe_claude"] == "2026-08-25T00:00:00Z", \
        "decline must never clear install probes"
    assert st["session_capture_receipt_claude"] == "2026-08-25T00:00:00Z", \
        "decline must never clear receipts (re-enable is receipt-authoritative)"


# ── Hosted team creation ────────────────────────────────────────

class TestOnboardingTeam:
    def test_create_team(self, client):
        r = client.post("/v1/onboarding/team", json={"name": "acme"})
        assert r.status_code == 200
        body = r.json()
        assert body.get("team_id") or body.get("id")
        assert body.get("name") == "acme"
        assert "key" not in body  # #1716: the response never carries a key

    def test_create_team_keyless_registry(self, client):
        """#1716 registry-lane parity: the sub-team is provisioned KEYLESS —
        no tt_ mint, no api_key hash on the Team node, no APIKey node (a
        minted key whose plaintext is never returned is an unrecoverable
        dead credential; the sub-team stays keyless until a session-key
        mint)."""
        r = client.post("/v1/onboarding/team", json={"name": "keyless"})
        assert r.status_code == 200
        body = r.json()
        assert "key" not in body
        # the registry-lane SDK is the CANONICAL control plane
        # (namespace="registry" → registry_control_plane — #1748: the old
        # namespace=team_id wrote a {team_id}_control_plane graph that no
        # other registry path reads, orphaning the sub-team) — query the
        # same graph the endpoint wrote to.
        reg = _make_sdk(namespace="registry")._get_registry()
        rows = reg.query(
            "MATCH (t:Team {name:'keyless'}) RETURN t.id, t.api_key",
        ).result_set
        assert len(rows) == 1
        tid, team_key_hash = rows[0]
        assert team_key_hash is None  # no dead key hash on the Team node
        n_keys = reg.query(
            "MATCH (k:APIKey {team_id:$tid}) RETURN count(k)",
            params={"tid": tid},
        ).result_set[0][0]
        assert n_keys == 0  # no APIKey node minted for the sub-team

    def test_team_name_validation(self, client):
        r = client.post("/v1/onboarding/team", json={"name": ""})
        assert r.status_code < 500

    def test_create_team_then_session_key_mint_registry(self, client):
        """#1748 registry-lane journey (the real #1716 escape hatch):
        create sub-team (keyless, but the session user is a REAL owner
        member — no throwaway identity, no hand-inserted membership) →
        session-key mint resolves the membership → the minted key resolves
        on REST → the sub-team is listable and deletable by its owner."""
        from tortoise.hosted_api import get_current_user
        r = client.post("/v1/onboarding/team", json={"name": "journey"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert "key" not in body  # #1716: keyless — no tt_ mint at onboarding
        sub_team_id = body["team_id"]
        # the session user is the owner member (registry Membership node in
        # the CANONICAL control plane — registry_control_plane)
        reg = _make_sdk(namespace="registry")._get_registry()
        rows = reg.query(
            "MATCH (m:Membership {team_id:$tid}) "
            "RETURN m.user_id, m.role, m.status",
            params={"tid": sub_team_id},
        ).result_set
        assert rows == [["user-1", "owner", "active"]], rows
        # no APIKey node minted for the keyless sub-team
        n_keys = reg.query(
            "MATCH (k:APIKey {team_id:$tid}) RETURN count(k)",
            params={"tid": sub_team_id},
        ).result_set[0][0]
        assert n_keys == 0
        # session-key mint (registry lane) — resolves the owner membership.
        # #1970 coverage preservation: under per-test isolation the registry
        # is FRESH, so user-1 has exactly ONE membership — the mint's
        # >1-membership disambiguation branch (the production multi-team
        # shape: a user with several memberships passes team_id) would go
        # silently dead. Seed a deterministic SECOND active owner membership
        # on another team so the branch is exercised, not inherited from
        # shared-session leftovers.
        import uuid
        seed_team_id = f"seed-team-{uuid.uuid4().hex[:8]}"
        assert seed_team_id != sub_team_id
        reg.query(
            "CREATE (t:Team {id:$tid, name:'seed-other', tier:'free'})",
            params={"tid": seed_team_id},
        )
        reg.query(
            "CREATE (m:Membership {team_id:$tid, user_id:'user-1', "
            "role:'owner', status:'active'})",
            params={"tid": seed_team_id},
        )
        # self-verifying precondition: the seed must be LIVE (the mint filters
        # user_id + status:'active' + team_id <> '' — a wrong shape silently
        # reverts to the single-membership branch, defeating the coverage
        # intent).
        n_active = reg.query(
            "MATCH (m:Membership {user_id:'user-1', status:'active'}) "
            "WHERE m.team_id <> '' RETURN count(m)",
        ).result_set[0][0]
        assert n_active == 2, "multi-team disambiguation seed must be active"
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": "user-1", "email": "user-1@example.com"}
        r2 = client.post("/v1/session/key", json={
            "purpose": "bootstrap", "team_id": sub_team_id})
        assert r2.status_code == 200, r2.text
        key = r2.json()["key"]
        assert key.startswith("tt_")
        assert r2.json()["team_id"] == sub_team_id
        # the minted key resolves on REST (registry APIKey node)
        app.dependency_overrides.clear()
        r3 = client.get("/v1/team",
                        headers={"Authorization": f"Bearer {key}"})
        assert r3.status_code == 200, r3.text
        assert r3.json()["team_id"] == sub_team_id
        # listable by the owner (GET /v1/teams)
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": "user-1", "email": "user-1@example.com"}
        r4 = client.get("/v1/teams")
        assert r4.status_code == 200, r4.text
        assert any(t["team_id"] == sub_team_id for t in r4.json())
        # deletable by the owner (DELETE /v1/teams/{id})
        r5 = client.delete(f"/v1/teams/{sub_team_id}")
        assert r5.status_code in (200, 202), r5.text

    def test_create_team_requires_session_user_registry(self, client):
        """#1748: no session user on the team context → 403 (never an
        owner-less orphan sub-team)."""
        from tortoise.hosted_api import get_current_team
        app.dependency_overrides[get_current_team] = lambda: {
            "team_id": "test-team-1", "tier": "free", "key_id": "k1",
            "max_users": 1, "max_graphs": 1, "max_teams": 1,
            "session_user_id": None,
        }
        r = client.post("/v1/onboarding/team", json={"name": "orphan"})
        assert r.status_code == 403, r.text
        reg = _make_sdk(namespace="registry")._get_registry()
        assert reg.query(
            "MATCH (t:Team {name:'orphan'}) RETURN count(t)",
        ).result_set[0][0] == 0

    def test_create_team_reentry_409(self, client):
        """#1970 falsification pin: the #1877 one-shot guard is production-
        intended, not a regression. A second sub-team create on the SAME
        team must 409 ("Sub-team already created") — the wizard creates the
        sub-team ONCE. NOTE: the guard reads the MAIN team's PERSISTED
        onboarding state and `_write_onboarding_state` is a MATCH...SET that
        silently no-ops when the parent Team node is absent (`team_create`
        provisions only the SUB-team node) — so the test seeds the parent
        node first; without the seed both POSTs 200 and the guard never
        fires."""
        _make_sdk(namespace="registry")._get_registry().query(
            "CREATE (t:Team {id:$id, onboarding_state:$st})",
            params={"id": "test-team-1", "st": "{}"},
        )
        r1 = client.post("/v1/onboarding/team", json={"name": "reentry-1"})
        assert r1.status_code == 200, r1.text
        # name validation runs BEFORE the guard — a valid name is required
        # to reach the 409 (an invalid/empty name would 400 instead).
        r2 = client.post("/v1/onboarding/team", json={"name": "reentry-2"})
        assert r2.status_code == 409, r2.text
        assert r2.json()["detail"] == "Sub-team already created"


# ── Register (self-service provisioning) ────────────────────────

class TestRegister:
    def test_register_invalid_email(self, client):
        r = client.post("/v1/register", json={"email": "not-an-email", "password": "x"})
        assert r.status_code < 500  # 400/422 validation, not crash

    def test_register_missing_fields(self, client):
        r = client.post("/v1/register", json={})
        assert r.status_code < 500


# ── #1727 Slice 2 (Task 11): STATE-KEY REGISTRATION TABLE ────────────
# Every capture-surface key must be registered in BOTH live default-state
# dicts + _ALLOWED_STATE_KEYS + the PATCH model — an unregistered key is
# silently dropped by the _update_onboarding_state allowlist filter. The
# parametrized test below makes the registration self-verifying.

# The plan's registration table: capture receipts (bare + per-harness),
# per-harness last-attempt failures, the re-ask flags, and the install
# probes. PATCH model fields use underscores (pydantic field names cannot
# carry hyphens) — the mapping table drives both the registration check and
# the PATCH round-trip.
# #1893: TYPE-AWARE tuple form — (patch_field, sample_value). The sample
# value is PATCHed and must round-trip (bool keys take True; timestamp keys
# take an ISO string; scope keys take a small non-empty sample).
_STATE_KEY_TABLE: dict[str, tuple[str, object]] = {
    "capture_revised": ("capture_revised", True),
    "capture_ask_shown": ("capture_ask_shown", True),
    "session_capture_receipt": ("session_capture_receipt", "2026-08-25T00:00:00Z"),
    "session_capture_receipt_claude": ("session_capture_receipt_claude", "2026-08-25T00:00:00Z"),
    "session_capture_receipt_claude-desktop": ("session_capture_receipt_claude_desktop", "2026-08-25T00:00:00Z"),
    "session_capture_receipt_claude-web": ("session_capture_receipt_claude_web", "2026-08-25T00:00:00Z"),
    "session_capture_receipt_codex": ("session_capture_receipt_codex", "2026-08-25T00:00:00Z"),
    "session_capture_receipt_cursor": ("session_capture_receipt_cursor", "2026-08-25T00:00:00Z"),
    "session_capture_receipt_pi": ("session_capture_receipt_pi", "2026-08-25T00:00:00Z"),
    "session_capture_last_error_claude": ("session_capture_last_error_claude", "2026-08-25T00:00:00Z"),
    "session_capture_last_error_claude-desktop": ("session_capture_last_error_claude_desktop", "2026-08-25T00:00:00Z"),
    "session_capture_last_error_claude-web": ("session_capture_last_error_claude_web", "2026-08-25T00:00:00Z"),
    "session_capture_last_error_codex": ("session_capture_last_error_codex", "2026-08-25T00:00:00Z"),
    "session_capture_last_error_cursor": ("session_capture_last_error_cursor", "2026-08-25T00:00:00Z"),
    "session_capture_last_error_pi": ("session_capture_last_error_pi", "2026-08-25T00:00:00Z"),
    "install_probe_claude": ("install_probe_claude", "2026-08-25T00:00:00Z"),
    "install_probe_pi": ("install_probe_pi", "2026-08-25T00:00:00Z"),
    # #1893: persisted source-scope keys (short repo names / {repo, branch}).
    "github_issues_scope": ("github_issues_scope", ["repo-a", "repo-b"]),
    "github_docs_scope": ("github_docs_scope", [{"repo": "repo-a", "branch": "main"}]),
}


def test_state_keys_registered_parametrized(client):
    """Task 11 (cycle-3 P1-2 fix, self-verifying): every capture-surface key
    round-trips through BOTH live default-state dicts, the allowlist, and the
    PATCH model — a key added to the table without registering it anywhere
    fails here (the allowlist filter would silently drop it in production)."""
    from tortoise.hosted_api import (
        _ALLOWED_STATE_KEYS,
        _ONBOARDING_DEFAULT_STATE,
        DEFAULT_ONBOARDING_STATE,
        OnboardingStatePatchRequest,
        _make_sdk,
    )
    from tortoise.sdk import TortoiseSDK  # noqa: F401 (module anchored)
    # Provision the Team node so the round-trip asserts REAL persistence
    # (the state writer is MATCH...SET — a silent no-op without the node;
    # mirrors the #1893 scope tests).
    _make_sdk(namespace="registry")._get_registry().query(
        "CREATE (t:Team {id:$id, onboarding_state:$st})",
        params={"id": "test-team-1", "st": "{}"},
    )
    for state_key, (patch_field, patch_value) in _STATE_KEY_TABLE.items():
        assert state_key in _ONBOARDING_DEFAULT_STATE, \
            f"{state_key} missing from _ONBOARDING_DEFAULT_STATE"
        assert state_key in DEFAULT_ONBOARDING_STATE, \
            f"{state_key} missing from DEFAULT_ONBOARDING_STATE (provision default)"
        assert state_key in _ALLOWED_STATE_KEYS, \
            f"{state_key} missing from _ALLOWED_STATE_KEYS"
        assert patch_field in OnboardingStatePatchRequest.model_fields, \
            f"{state_key} missing from the live PATCH model (field {patch_field})"
        # PATCH round-trip: the type-aware sample value must survive the
        # merge (bool keys take True; timestamp keys take an ISO string;
        # scope keys take a small non-empty sample) AND read back via GET
        # (the node is provisioned, so this is a real persisted round-trip).
        r = client.patch("/v1/onboarding/state",
                         json={patch_field: patch_value})
        assert r.status_code == 200, r.text
        assert r.json()["onboarding"][state_key] == patch_value, \
            f"{state_key} did not round-trip through PATCH"
        r = client.get("/v1/onboarding/state")
        assert r.json()["onboarding"][state_key] == patch_value, \
            f"{state_key} did not read back through GET"


def test_capture_surface_keys_shared_across_defaults():
    """Task 11: the two live default-state dicts expose the SAME capture
    surface — a key registered in one but not the other would diverge
    depending on whether the team was provisioned pre/post-registration."""
    from tortoise.hosted_api import (
        _ONBOARDING_DEFAULT_STATE,
        DEFAULT_ONBOARDING_STATE,
    )
    capture_keys = {k for k in _STATE_KEY_TABLE}
    assert capture_keys <= set(_ONBOARDING_DEFAULT_STATE)
    assert capture_keys <= set(DEFAULT_ONBOARDING_STATE)


# ── #1893: persisted GitHub source-scope keys ────────────────────────────
# The client fixture does NOT provision a Team node, and the state writer is
# MATCH...SET (a silent no-op without the node) — each test provisions
# test-team-1 explicitly (test_install_probe_round_trip pattern) so the
# PATCH→GET round-trips below assert REAL persistence, never in-memory-only
# responses.


def test_scope_keys_explicit_empty_round_trip(client):
    """#1893: [] is a VALID scope value (all repos) and must round-trip as
    [] — the persist path never omits empty (unlike the job builders).
    Provisions the Team node so the write is real, GETs between the seed
    and the clear so BOTH phases are pinned, and asserts the RAW STORED
    jsonb directly (a GET cannot distinguish absent-vs-[] because the
    defaults are [] — the raw read pins the wire form)."""
    import json as _json

    from tortoise.hosted_api import _make_sdk
    from tortoise.sdk import TortoiseSDK  # noqa: F401 (module anchored)
    registry = _make_sdk(namespace="registry")._get_registry()
    registry.query(
        "CREATE (t:Team {id:$id, onboarding_state:$st})",
        params={"id": "test-team-1", "st": "{}"},
    )
    # seed non-empty for BOTH keys — the selection must persist (journey
    # steps 1-2: multi-repo issues + named-branch docs round-trip)
    r = client.patch("/v1/onboarding/state", json={
        "github_issues_scope": ["repo-a", "repo-b"],
        "github_docs_scope": [{"repo": "repo-a", "branch": "dev"}],
    })
    assert r.status_code == 200, r.text
    r = client.get("/v1/onboarding/state")
    assert r.json()["onboarding"]["github_issues_scope"] == ["repo-a", "repo-b"]
    assert r.json()["onboarding"]["github_docs_scope"] == [{"repo": "repo-a", "branch": "dev"}]
    # clear to [] — the clear must land as [] not absent
    r = client.patch("/v1/onboarding/state",
                     json={"github_issues_scope": [], "github_docs_scope": []})
    assert r.status_code == 200, r.text
    got = r.json()["onboarding"]
    assert got["github_issues_scope"] == []
    assert got["github_docs_scope"] == []
    r = client.get("/v1/onboarding/state")
    assert r.json()["onboarding"]["github_issues_scope"] == []
    assert r.json()["onboarding"]["github_docs_scope"] == []
    # WIRE-FORM pin: the raw stored jsonb must contain both keys as [] — a
    # GET readback cannot detect a storage-layer omit-empty regression
    # (the merge default is already []), so read the node directly.
    rows = registry.query(
        "MATCH (t:Team {id:$id}) RETURN t.onboarding_state",
        params={"id": "test-team-1"},
    ).result_set
    assert len(rows) == 1, rows
    stored_raw = rows[0][0]
    stored = _json.loads(stored_raw) if isinstance(stored_raw, str) else (stored_raw or {})
    assert stored.get("github_issues_scope") == []
    assert stored.get("github_docs_scope") == []


def test_scope_keys_invalid_400(client):
    """#1893: PATCH-boundary validation — invalid repo/branch scope entries
    are rejected (400), never stored (mirrors the index endpoints). Seeds a
    valid scope first, then asserts the 400 attempts leave it intact (real
    storage, non-vacuous)."""
    from tortoise.hosted_api import _make_sdk
    from tortoise.sdk import TortoiseSDK  # noqa: F401 (module anchored)
    _make_sdk(namespace="registry")._get_registry().query(
        "CREATE (t:Team {id:$id, onboarding_state:$st})",
        params={"id": "test-team-1", "st": "{}"},
    )
    r = client.patch("/v1/onboarding/state", json={"github_issues_scope": ["ok-repo"]})
    assert r.status_code == 200, r.text
    r = client.patch("/v1/onboarding/state", json={"github_issues_scope": ["bad name!"]})
    assert r.status_code == 400, r.text
    # P3-1 contract: a blank entry INSIDE a non-empty list is rejected
    # (silently dropping it would turn a client bug into an org-wide diff)
    r = client.patch("/v1/onboarding/state", json={"github_issues_scope": ["ok-repo", ""]})
    assert r.status_code == 400, r.text
    r = client.patch("/v1/onboarding/state",
                     json={"github_docs_scope": [{"repo": "ok", "branch": "../../x"}]})
    assert r.status_code == 400, r.text
    r = client.patch("/v1/onboarding/state", json={"github_docs_scope": [{"repo": 123}]})
    assert r.status_code == 400, r.text
    r = client.patch("/v1/onboarding/state",
                     json={"github_docs_scope": [{"repo": "ok", "branch": 123}]})
    assert r.status_code == 400, r.text  # non-str branch → 400 (type-guard before strip, never 500)
    # pydantic boundary: a non-dict docs entry never reaches the validator
    # (list[dict] element type error) — pinned as the deliberate 422.
    r = client.patch("/v1/onboarding/state", json={"github_docs_scope": ["repo-a"]})
    assert r.status_code == 422, r.text
    r = client.get("/v1/onboarding/state")
    # the valid seed survived; nothing invalid was stored
    assert r.json()["onboarding"]["github_issues_scope"] == ["ok-repo"]
    assert r.json()["onboarding"]["github_docs_scope"] == []


def test_scope_branch_normalized_to_null(client):
    """#1893: a docs entry with branch "" (default contract) is persisted as
    null (normalized at the PATCH boundary) — GET returns null, and a repeat
    PATCH of the GET value is stable (no drift). Note: pydantic v2 lax mode
    coerces int→str, so `{"github_issues_scope": [123]}` would store
    ["123"] (a syntactically legal short name) — the 400 contract targets
    genuinely invalid inputs, not lax-coercible ones."""
    from tortoise.hosted_api import _make_sdk
    from tortoise.sdk import TortoiseSDK  # noqa: F401 (module anchored)
    _make_sdk(namespace="registry")._get_registry().query(
        "CREATE (t:Team {id:$id, onboarding_state:$st})",
        params={"id": "test-team-1", "st": "{}"},
    )
    r = client.patch("/v1/onboarding/state", json={
        "github_docs_scope": [{"repo": "repo-a", "branch": ""}]})
    assert r.status_code == 200, r.text
    # REAL persistence: GET reads back the normalized form (null, not "")
    r = client.get("/v1/onboarding/state")
    assert r.json()["onboarding"]["github_docs_scope"] == [{"repo": "repo-a", "branch": None}]
    # re-PATCH the GET value — stable (null stays null)
    r2 = client.patch("/v1/onboarding/state", json={
        "github_docs_scope": [{"repo": "repo-a", "branch": None}]})
    assert r2.status_code == 200, r2.text
    assert r2.json()["onboarding"]["github_docs_scope"] == [{"repo": "repo-a", "branch": None}]


def test_scope_keys_normalized_at_patch(client):
    """#1893: strip/dedupe normalization at the PATCH boundary — issues
    repos are stripped + deduped (_validate_repo_scope); docs entries are
    deduped by repo with a STRIPPED branch. Padded or duplicated values
    never persist."""
    from tortoise.hosted_api import _make_sdk
    from tortoise.sdk import TortoiseSDK  # noqa: F401 (module anchored)
    _make_sdk(namespace="registry")._get_registry().query(
        "CREATE (t:Team {id:$id, onboarding_state:$st})",
        params={"id": "test-team-1", "st": "{}"},
    )
    r = client.patch("/v1/onboarding/state", json={"github_issues_scope": [" a ", "a"]})
    assert r.status_code == 200, r.text
    assert r.json()["onboarding"]["github_issues_scope"] == ["a"]
    r = client.patch("/v1/onboarding/state", json={
        "github_docs_scope": [
            {"repo": "ok", "branch": " dev "},
            {"repo": "ok", "branch": "main"},
        ]})
    assert r.status_code == 200, r.text
    assert r.json()["onboarding"]["github_docs_scope"] == [{"repo": "ok", "branch": "dev"}]


def test_onboarding_defaults_fresh_lists(client):
    """#1893 (code-review P2): the list-typed default keys must NOT be shared
    across teams — _onboarding_defaults() returns fresh list objects per
    call, so an in-place mutation on one team's state never leaks into
    another team's defaults (or the module-level constant)."""
    from tortoise.hosted_api import _onboarding_defaults
    a = _onboarding_defaults()
    b = _onboarding_defaults()
    assert a["github_issues_scope"] == [] and b["github_issues_scope"] == []
    assert a["github_issues_scope"] is not b["github_issues_scope"]
    assert a["github_docs_scope"] is not b["github_docs_scope"]
    # mutating one default must not touch the module-level constant
    a["github_issues_scope"].append("leak")
    from tortoise.hosted_api import _ONBOARDING_DEFAULT_STATE
    assert _ONBOARDING_DEFAULT_STATE["github_issues_scope"] == []
    assert b["github_issues_scope"] == []


# ── #1727 Slice 2 (Task 14, T2-P1): install-probe round-trip ────────────


def test_install_probe_round_trip(client):
    """Task 14 (T2-P1): POST /v1/sessions/install-probe records the
    install_probe_{harness} REGISTERED state key (harness + server timestamp
    only — no content) and reads back. The probe is NOT consent-gated (it's
    install telemetry, so the dashboard can show install status before
    consent), but it IS get_current_team-gated (auth required — probes are
    per-team state)."""
    # Provision the Team node so state writes persist (the state writer is
    # MATCH...SET — a silent no-op without the node).
    from tortoise.hosted_api import _get_onboarding_state, _make_sdk
    from tortoise.sdk import TortoiseSDK  # noqa: F401 (module anchored)
    _make_sdk(namespace="registry")._get_registry().query(
        "CREATE (t:Team {id:$id, onboarding_state:$st})",
        params={"id": "test-team-1", "st": "{}"},
    )
    r = client.post("/v1/sessions/install-probe",
                    json={"harness": "claude"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["harness"] == "claude", body
    assert body["probe_at"], "server must stamp the probe time"
    state = _get_onboarding_state("test-team-1")
    assert state.get("install_probe_claude") == body["probe_at"], \
        "install_probe_claude must be recorded verbatim (server-stamped)"
    # pi probe lands on its own registered key (per-harness isolation).
    r2 = client.post("/v1/sessions/install-probe",
                     json={"harness": "pi"})
    assert r2.status_code == 200, r2.text
    state2 = _get_onboarding_state("test-team-1")
    assert state2.get("install_probe_pi") == r2.json()["probe_at"]


def test_install_probe_unregistered_harness_422(client):
    """Task 14: a harness with no REGISTERED install_probe_ key (codex /
    claude-desktop / claude-web / cursor — backfill-only or pending-spike
    harnesses) → 422 at the model boundary, never a silent drop (an
    unregistered key would be discarded by the allowlist filter and look
    like a recorded probe)."""
    from tortoise.hosted_api import _make_sdk
    _make_sdk(namespace="registry")._get_registry().query(
        "CREATE (t:Team {id:$id, onboarding_state:$st})",
        params={"id": "test-team-1", "st": "{}"},
    )
    r = client.post("/v1/sessions/install-probe",
                    json={"harness": "codex"})
    assert r.status_code == 422, r.text


def test_install_probe_requires_auth(unauth_client):
    """Task 14: the probe is get_current_team-gated — no auth, no probe."""
    r = unauth_client.post("/v1/sessions/install-probe",
                           json={"harness": "claude"})
    assert r.status_code == 401, r.text


def test_install_probe_not_gated_by_off_switch(client):
    """#1927: a team with recording DISABLED (session_recording=False)
    still records the probe — the probe is unconditional install telemetry
    (harness + timestamp only), deliberately NOT gated on the off-switch so
    the dashboard can show install status. The capture POST itself returns
    the off-switch 409."""
    from tortoise.hosted_api import _get_onboarding_state, _make_sdk, _update_onboarding_state
    _make_sdk(namespace="registry")._get_registry().query(
        "CREATE (t:Team {id:$id, onboarding_state:$st})",
        params={"id": "test-team-1", "st": "{}"},
    )
    _update_onboarding_state("test-team-1", session_recording=False)
    r = client.post("/v1/sessions/install-probe",
                    json={"harness": "claude"})
    assert r.status_code == 200, r.text
    assert _get_onboarding_state("test-team-1").get("install_probe_claude")
    # the off-switch is untouched: a disabled capture POST is a clear 409.
    r2 = client.post("/v1/sessions",
                     json={"conversation": [
                         {"role": "user", "content": "hello"}]})
    assert r2.status_code == 409, r2.text
    assert "disabled" in r2.json()["detail"]


def test_cross_surface_harness_vocab_contract():
    """#1727 (Task 11, T2-P2d): the analytics harness values are a subset of
    the SessionRequest harness Literal — one value set across surfaces (both
    code comments in hosted_api.py pin this contract; this test enforces it).
    Also asserts the dashboard's HARNESS_ORDER matches the analytics vocab, so
    the UI rows and the server can never drift to different harness names.
    """
    import re
    from pathlib import Path

    from tortoise.hosted_api import (
        _HARNESS_ANALYTICS_VALUES,
        _SESSION_HARNESS_VALUES,
    )

    # 1. server-side subset contract (analytics ⊆ SessionRequest Literal).
    assert _HARNESS_ANALYTICS_VALUES <= _SESSION_HARNESS_VALUES
    # both surfaces are exactly the pinned 6-harness vocabulary.
    assert set(_HARNESS_ANALYTICS_VALUES) == {
        "claude", "claude-desktop", "claude-web", "codex", "cursor", "pi",
    }
    assert frozenset({
        "claude", "claude-desktop", "claude-web", "codex", "cursor", "pi",
    }) == _SESSION_HARNESS_VALUES

    # 2. the frontend harness set matches the analytics vocab (parse the
    # dashboard constant — self-contained, no JS toolchain needed).
    root = Path(__file__).resolve().parent.parent
    harnesses_js = (root / "website/apps/dashboard/src/harnesses.js").read_text()
    m = re.search(r"export const HARNESS_ORDER\s*=\s*\[([^\]]*)\]", harnesses_js)
    assert m, "HARNESS_ORDER not found in website/apps/dashboard/src/harnesses.js"
    frontend = {s.strip().strip("'\"") for s in m.group(1).split(",") if s.strip()}
    assert frontend == set(_HARNESS_ANALYTICS_VALUES)
