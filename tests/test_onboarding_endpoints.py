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
    db_path = str(tmp_path / "onboarding.db")  # noqa: F841
    # Patch TortoiseSDK to use the temp DB (mirrors test_hosted_api.py)
    orig_init = TortoiseSDK.__init__

    def _patched(self, db_path_arg=None, *, namespace=None, db_path=None, **kw):
        # Isolate EVERY SDK construction (registry included) to the fixture's
        # temp DB — _make_sdk passes db_path= explicitly, and the registry
        # must not fall back to the shared temp default (that leaks state
        # across tests: "Team already exists").
        orig_init(self, db_path=db_path if db_path_arg is None else db_path_arg,
                  namespace=namespace, **kw)

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


@pytest.fixture
def unauth_client(tmp_path):
    """TestClient WITHOUT the auth override — real 401s."""
    db_path = str(tmp_path / "unauth.db")  # noqa: F841
    orig_init = TortoiseSDK.__init__

    def _patched(self, db_path_arg=None, *, namespace=None, db_path=None, **kw):
        # Isolate EVERY SDK construction (registry included) to the fixture's
        # temp DB — _make_sdk passes db_path= explicitly, and the registry
        # must not fall back to the shared temp default (that leaks state
        # across tests: "Team already exists").
        orig_init(self, db_path=db_path if db_path_arg is None else db_path_arg,
                  namespace=namespace, **kw)

    TortoiseSDK.__init__ = _patched
    with TestClient(app) as tc:
        yield tc
    TortoiseSDK.__init__ = orig_init


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
        """#1728 Slice 3 (single consent source): the session-recording
        endpoint writes the SAME consent keys as the wizard's sessions toggle
        — the enforced ``session_recording`` flag + ``capture_revised`` (a
        user-initiated enable is an explicit decision, so it resolves the
        exactly-once re-ask; the re-ask pane never re-shows for fresh
        opt-ins)."""
        r = client.post("/v1/onboarding/session-recording", json={"enabled": True})
        assert r.status_code == 200, r.text
        st = r.json()["onboarding"]
        assert st["session_recording"] is True
        assert st["capture_revised"] is True

    def test_disable_writes_capture_revised(self, client):
        """#1728: decline (Q3 no / wizard toggle-off) also writes
        ``capture_revised`` — the decline branch clears the enforced consent
        flag AND resolves the re-ask (mirrors the wizard/panel decline; same
        keys)."""
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
    from tortoise.hosted_api import _make_sdk, _get_onboarding_state
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
    import json
    from tortoise.hosted_api import app as _app, get_current_team
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
    # decline ⇒ capture POST is 403 (the enforced flag is the consent)
    from tortoise.hosted_api import _get_onboarding_state
    assert _get_onboarding_state("team-1728-re")["session_recording"] is False
    # re-enable via the tool — consent re-set despite capture_revised=True
    result, state2 = _invoke_session_recording_tool(tmp_path, "team-1728-re", True)
    assert "error" not in result, result
    assert state2["session_recording"] is True
    assert state2["capture_revised"] is True


def test_reask_gate_defaults_off(client):
    """#1728: the re-ask gate (session_recording=True && !capture_revised &&
    !capture_ask_shown) is OFF for a team that has never consented — fresh
    teams are never misled-flagged. (The fixture's embedded DB is shared in
    the carve-out lane, so the test resets its own state shape first.)"""
    r0 = client.patch("/v1/onboarding/state", json={
        "session_recording": False, "capture_revised": False,
        "capture_ask_shown": False})
    assert r0.status_code == 200, r0.text
    r = client.get("/v1/onboarding/state")
    assert r.status_code == 200
    st = r.json()["onboarding"]
    assert st["session_recording"] is False
    assert st["capture_revised"] is False
    assert st["capture_ask_shown"] is False
    assert not (st["session_recording"] and not st["capture_revised"]
                and not st["capture_ask_shown"])


def test_capture_revised_dedup(client):
    """#1728 (Journey 2): once ``capture_revised`` is set (any explicit
    resolution — answer OR fresh opt-in), the re-ask gate reads false even
    with the legacy consent flag still true: no cross-surface double-ask."""
    # reset to a known shape, then set the legacy-misled shape: consent on,
    # no resolution yet
    client.patch("/v1/onboarding/state", json={
        "session_recording": False, "capture_revised": False,
        "capture_ask_shown": False})
    r = client.patch("/v1/onboarding/state",
                     json={"session_recording": True})
    assert r.status_code == 200
    st = r.json()["onboarding"]
    assert st["session_recording"] is True
    assert st["capture_revised"] is False
    # the wizard's toggle-on PATCH resolves the re-ask in the same write
    r2 = client.patch("/v1/onboarding/state",
                      json={"session_recording": True, "capture_revised": True})
    assert r2.status_code == 200
    st2 = r2.json()["onboarding"]
    assert st2["capture_revised"] is True
    assert not (st2["session_recording"] and not st2["capture_revised"]
                and not st2["capture_ask_shown"]), \
        "resolved teams must never re-trigger the re-ask gate"
    # a second visit (fresh GET) still reads gate-false — no re-ask re-fire
    r3 = client.get("/v1/onboarding/state")
    st3 = r3.json()["onboarding"]
    assert not (st3["session_recording"] and not st3["capture_revised"]
                and not st3["capture_ask_shown"])


def test_decline_patch_clears_consent_keeps_receipts(client):
    """#1728 (T1-P8 + T2-P2e): the decline PATCH (re-ask NO / toggle-off)
    clears the enforced consent flag + sets capture_revised, but NEVER clears
    probes or receipts — re-enable resolves receipt-authoritative."""
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
        # Explicit team_id: the test registry is shared across tests in a
        # session (earlier tests' sub-teams leave user-1 memberships), so
        # the mint must disambiguate — exactly the production multi-team
        # shape (a user with several memberships passes team_id).
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
_STATE_KEY_TABLE: dict[str, str] = {
    "capture_revised": "capture_revised",
    "capture_ask_shown": "capture_ask_shown",
    "session_capture_receipt": "session_capture_receipt",
    "session_capture_receipt_claude": "session_capture_receipt_claude",
    "session_capture_receipt_claude-desktop": "session_capture_receipt_claude_desktop",
    "session_capture_receipt_claude-web": "session_capture_receipt_claude_web",
    "session_capture_receipt_codex": "session_capture_receipt_codex",
    "session_capture_receipt_cursor": "session_capture_receipt_cursor",
    "session_capture_receipt_pi": "session_capture_receipt_pi",
    "session_capture_last_error_claude": "session_capture_last_error_claude",
    "session_capture_last_error_claude-desktop": "session_capture_last_error_claude_desktop",
    "session_capture_last_error_claude-web": "session_capture_last_error_claude_web",
    "session_capture_last_error_codex": "session_capture_last_error_codex",
    "session_capture_last_error_cursor": "session_capture_last_error_cursor",
    "session_capture_last_error_pi": "session_capture_last_error_pi",
    "install_probe_claude": "install_probe_claude",
    "install_probe_pi": "install_probe_pi",
}


def test_state_keys_registered_parametrized(client):
    """Task 11 (cycle-3 P1-2 fix, self-verifying): every capture-surface key
    round-trips through BOTH live default-state dicts, the allowlist, and the
    PATCH model — a key added to the table without registering it anywhere
    fails here (the allowlist filter would silently drop it in production)."""
    from tortoise.hosted_api import (
        DEFAULT_ONBOARDING_STATE,
        OnboardingStatePatchRequest,
        _ALLOWED_STATE_KEYS,
        _ONBOARDING_DEFAULT_STATE,
    )
    for state_key, patch_field in _STATE_KEY_TABLE.items():
        assert state_key in _ONBOARDING_DEFAULT_STATE, \
            f"{state_key} missing from _ONBOARDING_DEFAULT_STATE"
        assert state_key in DEFAULT_ONBOARDING_STATE, \
            f"{state_key} missing from DEFAULT_ONBOARDING_STATE (provision default)"
        assert state_key in _ALLOWED_STATE_KEYS, \
            f"{state_key} missing from _ALLOWED_STATE_KEYS"
        assert patch_field in OnboardingStatePatchRequest.model_fields, \
            f"{state_key} missing from the live PATCH model (field {patch_field})"
        # PATCH round-trip: a type-appropriate value must survive the merge
        # and read back (bool fields take True; timestamp keys take an ISO
        # string).
        patch_value = (True if state_key in ("capture_revised", "capture_ask_shown")
                       else "2026-08-25T00:00:00Z")
        r = client.patch("/v1/onboarding/state",
                         json={patch_field: patch_value})
        assert r.status_code == 200, r.text
        assert r.json()["onboarding"][state_key] == patch_value, \
            f"{state_key} did not round-trip through PATCH"


def test_capture_surface_keys_shared_across_defaults():
    """Task 11: the two live default-state dicts expose the SAME capture
    surface — a key registered in one but not the other would diverge
    depending on whether the team was provisioned pre/post-registration."""
    from tortoise.hosted_api import (
        DEFAULT_ONBOARDING_STATE,
        _ONBOARDING_DEFAULT_STATE,
    )
    capture_keys = {k for k in _STATE_KEY_TABLE}
    assert capture_keys <= set(_ONBOARDING_DEFAULT_STATE)
    assert capture_keys <= set(DEFAULT_ONBOARDING_STATE)


# ── #1727 Slice 2 (Task 14, T2-P1): install-probe round-trip ────────────


def test_install_probe_round_trip(client):
    """Task 14 (T2-P1): POST /v1/sessions/install-probe records the
    install_probe_{harness} REGISTERED state key (harness + server timestamp
    only — no content) and reads back. The probe is NOT consent-gated (it's
    install telemetry, so the dashboard can show install status before
    consent), but it IS get_current_team-gated (auth required — probes are
    per-team state)."""
    from tortoise.hosted_api import _get_onboarding_state
    from tortoise.sdk import TortoiseSDK  # noqa: F401 (module anchored)
    # Provision the Team node so state writes persist (the state writer is
    # MATCH...SET — a silent no-op without the node).
    from tortoise.hosted_api import _make_sdk
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


def test_install_probe_not_consent_gated(client):
    """Task 14: an UN-OPTED team (session_recording=False) still records the
    probe — the probe is unconditional install telemetry (harness + timestamp
    only), deliberately NOT consent-gated so the dashboard can show install
    status before consent. The capture POST itself remains 403-gated."""
    from tortoise.hosted_api import _get_onboarding_state, _make_sdk
    from tortoise.hosted_api import _update_onboarding_state
    _make_sdk(namespace="registry")._get_registry().query(
        "CREATE (t:Team {id:$id, onboarding_state:$st})",
        params={"id": "test-team-1", "st": "{}"},
    )
    _update_onboarding_state("test-team-1", session_recording=False)
    r = client.post("/v1/sessions/install-probe",
                    json={"harness": "claude"})
    assert r.status_code == 200, r.text
    assert _get_onboarding_state("test-team-1").get("install_probe_claude")
    # the consent gate is untouched: an un-opted capture POST is still 403.
    r2 = client.post("/v1/sessions",
                     json={"conversation": [
                         {"role": "user", "content": "hello"}]})
    assert r2.status_code == 403, r2.text
