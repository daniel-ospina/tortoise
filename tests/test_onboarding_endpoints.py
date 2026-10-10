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

from tests._http_fixtures import patched_tortoise_sdk
from tortoise.hosted_api import app, _make_sdk
from tortoise.sdk import TortoiseSDK


def _close_keepalive_anchors(anchors: dict) -> None:
    """Deterministically close every keepalive anchor (SHUTDOWN SAVE).

    #2090: replaces the clear-without-close leak (each eviction shut the
    redislite daemon down mid-test → empty reads / 403s). # mirrors
    tests/test_hosted_api.py:144-153 and tests/test_export_delete.py.
    Accepts the ``_FALLBACK_KEEPALIVE`` dict (callers import it directly).
    """
    for ns in list(anchors):
        anchor = anchors.pop(ns, None)
        if anchor is not None:
            try:  # noqa: SIM105
                anchor.close()
            except Exception:
                pass


@pytest.fixture
def client(tmp_path):
    """TestClient with a temp embedded DB + registry."""
    fixture_db_path = str(tmp_path / "onboarding.db")
    # #2127 wave 2: shared helper — patch __init__ → temp DB, #1950
    # TORTOISE_DB_PATH pin, close-then-clear at enter; pop-env → restore
    # __init__ → deterministic anchor close → clear overrides at exit.
    # Supersedes the inline canonical trio (patch → clear → pin → restore →
    # pop → close) — this fixture was already #2090-canonical; the helper is
    # the single source of truth now. The override must stay inside the
    # helper so its exit clear covers failure paths too.
    with patched_tortoise_sdk(fixture_db_path):
        from tortoise.hosted_api import get_current_org
        app.dependency_overrides[get_current_org] = lambda: {
            "org_id": "test-team-1", "tier": "free", "key_id": "k1",
            # C5 #2114: key_id-bearing dicts must carry the C2 owner class —
            # deleg-NULL + scopes [] resolves legacy_full_access True at auth
            # time (see test_hosted_api TEST_TEAM). A scope-less minted shape
            # would 403 the C5 _require_scope gates on the seeded endpoints.
            "legacy_full_access": True, "max_users": 1, "max_graphs": 1,
            "max_teams": 1,
            # #1922: the demo seed is now quota-gated — the team dict must
            # carry max_points (the fail-closed points cap) or the check
            # 500s. #4010: the same contract now applies to max_sessions
            # (unlimited → explicit None); a missing key is fail-closed.
            "max_points": 10000,
            "max_sessions": None,
            # #1748: the onboarding sub-team is provisioned on the USER path
            # — the session user becomes the owner member
            # (get_current_org_session attaches session_user_id for session
            # JWT auth; tests seed it here).
            "session_user_id": "user-1",
        }
        with TestClient(app) as tc:
            yield tc


@pytest.fixture
def unauth_client(tmp_path):
    """TestClient WITHOUT the auth override — real 401s."""
    fixture_db_path = str(tmp_path / "unauth.db")
    # #2127 wave 2: shared helper (see the client fixture — the fixture was
    # already canonical; the helper now owns the patch/pin/close cycle).
    with patched_tortoise_sdk(fixture_db_path), TestClient(app) as tc:
        yield tc


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

    def test_demo_seed_402_at_cap(self, client):
        """#1922 quota: a team at the points cap gets 402 — the demo seed
        must not be the quota bypass (MCP twin gates the same write).

        Uses a per-test unique TEAM id (not test-*): under the docker-lane
        redirect (epic #1647) a test-* namespace maps to a shared verbatim
        server graph, so a fresh graph per test requires a unique team id
        (the per-path redirect derivation isolates it)."""
        import uuid

        from tortoise.hosted_api import _make_sdk, app, get_current_org
        tid = f"team-demo-{uuid.uuid4().hex[:8]}"
        app.dependency_overrides[get_current_org] = lambda tid=tid: {
            "org_id": tid, "tier": "free", "key_id": "k1",
            "legacy_full_access": True,
            "max_users": 1, "max_graphs": 1, "max_teams": 1,
            "max_points": 0,  # at cap — count(0) >= limit(0)
            "max_sessions": None,
        }
        r = client.post("/v1/demo")
        assert r.status_code == 402, r.text
        # Nothing may have been written: no sentinel, no metering record.
        sdk = _make_sdk(namespace=tid)
        sent = sdk._get_proj().g.query(
            "MATCH (p:Point {id: '_demo_sentinel'}) RETURN p.id"
        ).result_set
        assert not sent, "quota-gated demo seed must not write points"
        rows = _make_sdk(namespace="registry")._get_registry().query(
            "MATCH (m:MeteringRecord {org_id:$tid}) RETURN m.write_ops",
            params={"tid": tid},
        ).result_set
        assert not rows, "quota-gated demo seed must not record write ops"

    def test_demo_seed_records_write_ops(self, client):
        """#1922 metering: a successful demo seed records one write op with
        the seeded node count (12 points + the _demo_sentinel). Uses a
        per-test unique team id so the docker-lane redirect derives a fresh
        per-path server graph (see test_demo_seed_402_at_cap)."""
        import uuid

        from tortoise.hosted_api import _make_sdk, app, get_current_org
        tid = f"team-demo-{uuid.uuid4().hex[:8]}"
        app.dependency_overrides[get_current_org] = lambda tid=tid: {
            "org_id": tid, "tier": "free", "key_id": "k1",
            "legacy_full_access": True,
            "max_users": 1, "max_graphs": 1, "max_teams": 1,
            "max_points": 10000,
            "max_sessions": None,
        }
        r = client.post("/v1/demo")
        assert r.status_code == 200, r.text
        rows = _make_sdk(namespace="registry")._get_registry().query(
            "MATCH (m:MeteringRecord {org_id:$tid}) "
            "RETURN m.write_ops, m.nodes_written",
            params={"tid": tid},
        ).result_set
        assert rows, "expected a MeteringRecord after demo seed"
        write_ops, nodes_written = int(rows[0][0]), int(rows[0][1])
        assert write_ops == 1
        assert nodes_written == 13  # 12 seeded points + _demo_sentinel


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


def _invoke_session_recording_tool(tmp_path, org_id: str, enabled: bool):
    """Invoke the MCP tool the way Q3 executes it (HTTP mode team context)
    against an isolated temp SDK, returning (result, state_after)."""
    from tortoise.mcp_auth import _current_org_id
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
        params={"id": org_id, "st": "{}"},
    )
    tok = _current_org_id.set(org_id)
    try:
        result = tortoise_onboarding_session_recording(enabled=enabled)
        state = _get_onboarding_state(org_id)
    finally:
        _current_org_id.reset(tok)
        _close_keepalive_anchors(_FALLBACK_KEEPALIVE)
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
    # ⚠️ #2127 wave-2 audit (named block): this is a DELIBERATE db_path
    # PASS-THROUGH patch — the PATCH flow must bind the SAME registry DB the
    # Q3 tool block above used (the assertions below compare cross-surface
    # state), so it is NOT migrated onto the force-db_path shared helper.
    # GAP FIXED: the old restore restored __init__ WITHOUT closing the
    # anchor its TestClient registry read creates while TORTOISE_DB_PATH is
    # UNSET → bound the SHARED default path (#1497/#2090 shared-DB leak
    # class). The finally now closes every keepalive anchor deterministically
    # (same discipline as _invoke_session_recording_tool's restore) — the
    # shared binding while ACTIVE is the deliberate pass-through; the anchor
    # no longer survives into the next test.
    from tortoise.hosted_api import _FALLBACK_KEEPALIVE, get_current_org
    from tortoise.hosted_api import app as _app
    orig_init = TortoiseSDK.__init__

    def _patched(self, db_path_arg=None, *, namespace=None, db_path=None, **kw):
        orig_init(self, db_path=db_path if db_path_arg is None else db_path_arg,
                  namespace=namespace, **kw)

    TortoiseSDK.__init__ = _patched
    _app.dependency_overrides[get_current_org] = lambda: {
        "org_id": "team-1728-q3", "tier": "free", "key_id": "k1",
            "legacy_full_access": True,
    }
    try:
        with TestClient(_app) as tc:
            r = tc.patch("/v1/onboarding/state",
                         json={"session_recording": True, "capture_revised": True})
            assert r.status_code == 200, r.text
            st = r.json()["onboarding"]
    finally:
        _app.dependency_overrides.clear()
        _close_keepalive_anchors(_FALLBACK_KEEPALIVE)
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


# ── #3540 — tool-surface enable parity + the tool-path partition ─────────
# Epic #1714 R6: the tool surface and the dashboard must write IDENTICAL
# state keys for the same enable intent (one state path), and NO tool
# enable/connect path may write a SERVER-OWNED key — #3552's partition is the
# contract, and a tool that could set a receipt/probe/cursor is the same
# forgery R11 forbids. #3552 owns the PATCH partition; these assert the TOOL
# half of it.


def _spy_state_router(monkeypatch) -> dict[str, set[str]]:
    """Capture the state keys each surface hands to the ONE router.

    Both the MCP tool and the PATCH route import/look up
    ``_update_onboarding_state`` at call time, so patching the module
    attribute intercepts BOTH while recording the exact key set each wrote.
    """
    import tortoise.hosted_api as ha

    captured: dict[str, set[str]] = {}

    def _spy(org_id, *a, **fields):
        captured.setdefault(org_id, set()).update(fields)
        return {}

    monkeypatch.setattr(ha, "_update_onboarding_state", _spy, raising=True)
    return captured


def test_tool_and_dashboard_enable_write_same_keys(client, monkeypatch):
    """#3540: enabling the session-recording integration from the MCP tool
    (``tortoise_onboarding_session_recording``) and from the dashboard PATCH
    write IDENTICAL state keys for the same intent — one state path, no
    cross-surface divergence (mirrors #3517's prompt/dashboard parity)."""
    from tortoise import mcp_server
    from tortoise.mcp_auth import _current_org_id

    captured = _spy_state_router(monkeypatch)

    tok = _current_org_id.set("team-3540-tool")
    try:
        result = mcp_server.tortoise_onboarding_session_recording(enabled=True)
    finally:
        _current_org_id.reset(tok)
    assert "error" not in result, result

    r = client.patch("/v1/onboarding/state",
                     json={"session_recording": True, "capture_revised": True})
    assert r.status_code == 200, r.text

    tool_keys = captured.get("team-3540-tool")
    dashboard_keys = captured.get("test-team-1")
    assert tool_keys, "the tool path never reached the state router"
    assert dashboard_keys, "the dashboard path never reached the state router"
    assert tool_keys == dashboard_keys, (
        f"tool wrote {sorted(tool_keys)} but dashboard wrote "
        f"{sorted(dashboard_keys)}")


def test_tool_enable_paths_never_write_server_owned_keys(monkeypatch):
    """#3540 (epic cycle-6 mandate 1 / R11): the tool enable/connect paths
    carry the SAME three-class partition as PATCH — no key a tool hands to the
    onboarding-state router may be SERVER-OWNED (a receipt, a probe, an index
    cursor, the CAS token), so a tool path cannot forge a capture status.
    Also asserts no enable tool admits ``**kwargs``, so an arbitrary state key
    cannot be smuggled through the tool surface."""
    import inspect

    from tortoise import mcp_server
    from tortoise.hosted_api import (
        _ALLOWED_STATE_KEYS,
        _PATCH_SERVER_OWNED_KEYS,
    )
    from tortoise.mcp_auth import _current_org_id

    captured = _spy_state_router(monkeypatch)
    tok = _current_org_id.set("team-3540-partition")
    try:
        result = mcp_server.tortoise_onboarding_session_recording(enabled=True)
    finally:
        _current_org_id.reset(tok)
    assert "error" not in result, result

    written = captured.get("team-3540-partition") or set()
    assert written, "the tool path never reached the state router"
    # The tool's own write surface is EXACTLY its two documented keys —
    # non-vacuous, and disjoint from the server-owned set.
    assert written == {"session_recording", "capture_revised"}, sorted(written)
    assert written <= _ALLOWED_STATE_KEYS
    assert written.isdisjoint(_PATCH_SERVER_OWNED_KEYS), sorted(
        written & _PATCH_SERVER_OWNED_KEYS)

    # Non-vacuity of the server-owned set: the capture-evidence family is in
    # it, and the CAS token is deliberately NOT a state key at all — so the
    # partition above is a real filter, not an empty set.
    assert "session_capture_receipt" in _PATCH_SERVER_OWNED_KEYS
    assert "state_version" not in _ALLOWED_STATE_KEYS

    for tool in (mcp_server.tortoise_onboarding_session_recording,
                 mcp_server.tortoise_onboarding_github_connect,
                 mcp_server.tortoise_onboarding_github_index):
        params = inspect.signature(tool).parameters.values()
        assert not any(p.kind is inspect.Parameter.VAR_KEYWORD
                       for p in params), (
            f"{tool.__name__} accepts **kwargs — an arbitrary state key could "
            "be smuggled through the tool surface")


def test_fresh_team_defaults_to_recording_on(client):
    """#1927: a FRESH team (no stored flag) reads session_recording=True
    from the default merge — capture works out of the box, no consent gate.
    (The carve-out lane's embedded DB is shared across tests, so the team id
    is unique per run to prove the default rather than inherited state.)"""
    import uuid

    from tortoise.hosted_api import _get_onboarding_state, _make_sdk
    org_id = f"test-team-1927-default-{uuid.uuid4().hex[:8]}"
    _make_sdk(namespace="registry")._get_registry().query(
        "CREATE (t:Team {id:$id, onboarding_state:$st})",
        params={"id": org_id, "st": "{}"},
    )
    st = _get_onboarding_state(org_id)
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
        assert body.get("org_id") or body.get("id")
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
        # namespace=org_id wrote a {org_id}_control_plane graph that no
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
            "MATCH (k:APIKey {org_id:$tid}) RETURN count(k)",
            params={"tid": tid},
        ).result_set[0][0]
        assert n_keys == 0  # no APIKey node minted for the sub-team

    def test_org_name_validation(self, client):
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
        sub_org_id = body["org_id"]
        # the session user is the owner member (registry Membership node in
        # the CANONICAL control plane — registry_control_plane)
        reg = _make_sdk(namespace="registry")._get_registry()
        rows = reg.query(
            "MATCH (m:Membership {org_id:$tid}) "
            "RETURN m.user_id, m.role, m.status",
            params={"tid": sub_org_id},
        ).result_set
        assert rows == [["user-1", "owner", "active"]], rows
        # no APIKey node minted for the keyless sub-team
        n_keys = reg.query(
            "MATCH (k:APIKey {org_id:$tid}) RETURN count(k)",
            params={"tid": sub_org_id},
        ).result_set[0][0]
        assert n_keys == 0
        # session-key mint (registry lane) — resolves the owner membership.
        # #1970 coverage preservation: under per-test isolation the registry
        # is FRESH, so user-1 has exactly ONE membership — the mint's
        # >1-membership disambiguation branch (the production multi-team
        # shape: a user with several memberships passes org_id) would go
        # silently dead. Seed a deterministic SECOND active owner membership
        # on another team so the branch is exercised, not inherited from
        # shared-session leftovers.
        import uuid
        seed_org_id = f"seed-team-{uuid.uuid4().hex[:8]}"
        assert seed_org_id != sub_org_id
        reg.query(
            "CREATE (t:Team {id:$tid, name:'seed-other', tier:'free'})",
            params={"tid": seed_org_id},
        )
        reg.query(
            "CREATE (m:Membership {org_id:$tid, user_id:'user-1', "
            "role:'owner', status:'active'})",
            params={"tid": seed_org_id},
        )
        # self-verifying precondition: the seed must be LIVE (the mint filters
        # user_id + status:'active' + org_id <> '' — a wrong shape silently
        # reverts to the single-membership branch, defeating the coverage
        # intent).
        n_active = reg.query(
            "MATCH (m:Membership {user_id:'user-1', status:'active'}) "
            "WHERE m.org_id <> '' RETURN count(m)",
        ).result_set[0][0]
        assert n_active == 2, "multi-team disambiguation seed must be active"
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": "user-1", "email": "user-1@example.com"}
        r2 = client.post("/v1/session/key", json={
            "purpose": "bootstrap", "org_id": sub_org_id})
        assert r2.status_code == 200, r2.text
        key = r2.json()["key"]
        assert key.startswith("tt_")
        assert r2.json()["org_id"] == sub_org_id
        # the minted key resolves on REST (registry APIKey node)
        app.dependency_overrides.clear()
        r3 = client.get("/v1/team",
                        headers={"Authorization": f"Bearer {key}"})
        assert r3.status_code == 200, r3.text
        assert r3.json()["org_id"] == sub_org_id
        # listable by the owner (GET /v1/organizations)
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": "user-1", "email": "user-1@example.com"}
        r4 = client.get("/v1/organizations")
        assert r4.status_code == 200, r4.text
        assert any(t["org_id"] == sub_org_id for t in r4.json())
        # deletable by the owner (DELETE /v1/organizations/{id})
        r5 = client.delete(f"/v1/organizations/{sub_org_id}")
        assert r5.status_code in (200, 202), r5.text

    def test_create_team_requires_session_user_registry(self, client):
        """#1748: no session user on the team context → 403 (never an
        owner-less orphan sub-team)."""
        from tortoise.hosted_api import get_current_org
        app.dependency_overrides[get_current_org] = lambda: {
            "org_id": "test-team-1", "tier": "free", "key_id": "k1",
            # C5 #2114: key_id-bearing dicts must carry the C2 owner class —
            # deleg-NULL + scopes [] resolves legacy_full_access True at auth
            # time (see test_hosted_api TEST_TEAM). A scope-less minted shape
            # would 403 the C5 _require_scope gates on the seeded endpoints.
            "legacy_full_access": True, "max_users": 1, "max_graphs": 1,
            "max_teams": 1,
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
    # #1924: per-source ENABLE intent. Unlike every other bool in this table —
    # which samples True — these PATCH False on purpose: the OFF direction IS
    # the bug the key exists to fix, so the round-trip must prove a False
    # survives the merge (True-only coverage would never exercise off).
    "issues_enabled": ("issues_enabled", False),
    "docs_enabled": ("docs_enabled", False),
    # #1894: last-indexed timestamps (ISO strings, server-stamped at
    # completion — registered like every other capture/state key).
    "github_indexed_at": ("github_indexed_at", "2026-08-25T00:00:00Z"),
    "github_docs_indexed_at": ("github_docs_indexed_at", "2026-08-25T00:00:00Z"),
    "capture_revised": ("capture_revised", True),
    "capture_ask_shown": ("capture_ask_shown", True),
    # #4258: the per-org "capture also extracts into memory" user setting.
    # The sample is the NON-default (False) — with the default True a dropped
    # write would be masked by the GET merge, so the round-trip could not fail.
    "capture_extract": ("capture_extract", False),
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
    """Task 11 (cycle-3 P1-2 fix, self-verifying): every capture-surface key is
    REGISTERED — present in BOTH live default-state dicts, the allowlist, and
    the PATCH model — so a key added to the table without registering it
    anywhere fails here (the allowlist filter would otherwise silently drop it
    in production). The OPERATIONAL keys then round-trip through PATCH + GET;
    the server-owned capture/install evidence keys are covered here for
    REGISTRATION only and skipped for the round-trip — their refusal (403, no
    write) is pinned with the value assertion this test never made, in
    ``test_capture_verification_keys_not_client_writable`` below."""
    from tortoise.hosted_api import (
        _ALLOWED_STATE_KEYS,
        _CAPTURE_SERVER_OWNED_KEYS,
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
        #
        # #3681/#3552: the capture/install EVIDENCE keys (receipts, per-harness
        # last-errors, install probes) are SERVER-OWNED. The registration
        # assertions above still apply to them IN FULL — they must exist on
        # both default dicts, the allowlist and the PATCH model — but the PATCH
        # half of this test now covers CLIENT-WRITABLE keys only (#3552 Target:
        # "the parametrized registration test covers client-writable keys
        # only"). Their refusal is pinned — together with the value assertion
        # this test never made — in
        # ``test_capture_verification_keys_not_client_writable`` below.
        if state_key in _CAPTURE_SERVER_OWNED_KEYS:
            continue
        r = client.patch("/v1/onboarding/state",
                         json={patch_field: patch_value})
        assert r.status_code == 200, r.text
        assert r.json()["onboarding"][state_key] == patch_value, \
            f"{state_key} did not round-trip through PATCH"
        r = client.get("/v1/onboarding/state")
        assert r.json()["onboarding"][state_key] == patch_value, \
            f"{state_key} did not read back through GET"


def test_capture_verification_keys_not_client_writable(client):
    """#3552 Target — an authenticated client PATCH cannot green the capture
    surface: every ``_CAPTURE_SERVER_OWNED_KEYS`` member is refused with 403
    ``server_owned_key`` **and leaves the stored value UNCHANGED**.

    Why the value half is the assertion that matters, and cannot be folded
    into the registration test above: a 403 that still MUTATED the row would
    satisfy any status-only check, and these are exactly the keys that make
    the capture sentence's TENSE true in ``captureStatus.js`` — a
    client-writable receipt fabricates a present-tense claim with nothing
    filed. That is the #3671 false-claim class, one key further down (#3681).

    The endpoint-accepts-writes control is deliberately NOT re-added here. A
    refusal test is satisfiable by a route that simply never writes anything,
    so it is only meaningful beside the writes that DO land — and those already
    exist and pass: ``test_install_probe_round_trip`` (BELOW this test in the
    file) proves ``POST /v1/sessions/install-probe`` still records
    ``install_probe_{h}``, ``test_install_probe_unregistered_harness_422`` pins
    the supported-harnesses-only asymmetry as DELIBERATE rather than a coverage
    gap, and the ``POST /v1/sessions`` 2xx path is proven to record receipts in
    ``tests/test_capture_session.py`` (truthy ``session_capture_receipt_claude``
    assertions). The off-switch test in THIS file is deliberately NOT cited as
    a positive control: it only asserts ``receipts_after_409 ==
    receipts_after_on``, which passes empty-vs-empty.

    What this test adds over the existing refusal coverage
    (``test_onboarding_truth_surface.py::TestPatchRefusesFabricatedReceipt`` —
    stronger on the CREDENTIAL axis, parametrized over the agent-key and
    session-JWT lanes and asserting the state setter received NOTHING at all)
    is the PERSISTED-VALUE assertion against a provisioned node, which a
    monkeypatched setter call cannot make. The two are complements, not two
    independent coverages.
    """
    from tortoise.hosted_api import (
        _CAPTURE_SERVER_OWNED_KEYS,
        _make_sdk,
        _update_onboarding_state,
    )
    _make_sdk(namespace="registry")._get_registry().query(
        "CREATE (t:Team {id:$id, onboarding_state:$st})",
        params={"id": "test-team-1", "st": "{}"},
    )
    # Coverage guard: every DERIVED server-owned key must be exercised here.
    # A harness added to _SESSION_HARNESS_VALUES mints new receipt/last-error
    # members, and ``install_probe_*`` is derived from the registration table
    # — so a key can become server-owned with no PATCH field to send it under,
    # which would leave it silently untested (the exact hole the split closes).
    uncovered = _CAPTURE_SERVER_OWNED_KEYS - set(_STATE_KEY_TABLE)
    assert not uncovered, (
        "server-owned key(s) with no PATCH field in _STATE_KEY_TABLE, so their "
        f"refusal cannot be asserted: {sorted(uncovered)}")
    # ...and the OTHER direction, which the guard above cannot see. Dropping a
    # key from the DERIVATION makes it client-writable while leaving THIS test
    # vacuously green (the loop iterates the derived set) and the registration
    # test green too (a client-writable key simply round-trips, which is what
    # it asserts). Three checks follow and their strengths DIFFER — measured,
    # not assumed. Only the middle one is independent:
    #   * the harness loop is NOT independent: _CAPTURE_SERVER_OWNED_KEYS is
    #     computed FROM _SESSION_HARNESS_VALUES, so dropping a harness shrinks
    #     the loop along with the mutation and it stays GREEN (reproduced). It
    #     catches a hand-edited comprehension, not a vocabulary change;
    #   * `_evidence` below IS independent, and it is the check that actually
    #     closes this direction: it derives the receipt/last-error family from
    #     _ALLOWED_STATE_KEYS — a DIFFERENT source from the
    #     _SESSION_HARNESS_VALUES that production derives from — so an evidence
    #     key registered without becoming server-owned is RED here (reproduced:
    #     dropping "pi" reddens at _evidence while the loop stays green);
    #   * `_probes` is a HAND-EDIT CANARY of the same class as the loop, NOT an
    #     independence check: it is one of the union TERMS of
    #     _CAPTURE_SERVER_OWNED_KEYS — character-identical over the same frozen
    #     set — so it holds by construction and cannot fail on any
    #     registration-driven change.
    # A vocabulary change is also caught outside this file
    # (test_cross_surface_harness_vocab_contract pins the exact 6-member set;
    # two test_5051 harness-set assertions), so these are defence in depth
    # rather than the primary guard.
    from tortoise.hosted_api import (
        _ALLOWED_STATE_KEYS,
        _SESSION_HARNESS_VALUES,
    )
    assert "session_capture_receipt" in _CAPTURE_SERVER_OWNED_KEYS, (
        "the bare (harness-less) receipt is server-owned — the legacy hooks' "
        "member and the session-JWT lane's member")
    for _h in _SESSION_HARNESS_VALUES:
        assert f"session_capture_receipt_{_h}" in _CAPTURE_SERVER_OWNED_KEYS, (
            f"receipt for harness {_h!r} is NOT server-owned — it became "
            "client-writable, so a PATCH can fabricate a capture")
        assert f"session_capture_last_error_{_h}" in _CAPTURE_SERVER_OWNED_KEYS, (
            f"last-error for harness {_h!r} is NOT server-owned — it became "
            "client-writable")
    # Derive the same family from the REGISTRATION surface, which the loop
    # above cannot do for itself (see the note): this is the INDEPENDENT check
    # and what catches a receipt key registered for a harness the vocabulary
    # does not name.
    _evidence = {k for k in _ALLOWED_STATE_KEYS
                 if k.startswith(("session_capture_receipt",
                                  "session_capture_last_error"))}
    assert _evidence <= _CAPTURE_SERVER_OWNED_KEYS, (
        "registered capture-evidence keys that are NOT server-owned, so a "
        f"PATCH can fabricate one: {sorted(_evidence - _CAPTURE_SERVER_OWNED_KEYS)}")
    _probes = {k for k in _ALLOWED_STATE_KEYS if k.startswith("install_probe_")}
    assert _probes <= _CAPTURE_SERVER_OWNED_KEYS, (
        "install probes missing from the server-owned set, so a PATCH can "
        f"claim an install that never happened: {sorted(_probes - _CAPTURE_SERVER_OWNED_KEYS)}")

    # A value DISTINCT from the sample the table PATCHes, so "unchanged" is a
    # real assertion rather than a comparison of two identical writes.
    sentinel = "2020-01-01T00:00:00Z"
    for state_key in sorted(_CAPTURE_SERVER_OWNED_KEYS):
        patch_field, sample = _STATE_KEY_TABLE[state_key]
        assert sample != sentinel, (
            f"{state_key}: the sentinel must differ from the PATCHed sample, "
            "or 'unchanged' proves nothing")
        # The TRUSTED path writes it — this is what a client must not reach.
        _update_onboarding_state(
            "test-team-1", _echo=False, **{state_key: sentinel})
        before = client.get("/v1/onboarding/state").json()["onboarding"]
        assert before.get(state_key) == sentinel, (
            f"{state_key}: the server-side write did not land "
            f"({before.get(state_key)!r}) — the assertions below would be "
            "vacuous against a value that was never there")
        r = client.patch("/v1/onboarding/state", json={patch_field: sample})
        assert r.status_code == 403, (
            f"server-owned key {state_key} was client-writable: {r.text}")
        assert r.json()["detail"] == {
            "message": "server_owned_key", "keys": [state_key]}, r.text
        after = client.get("/v1/onboarding/state").json()["onboarding"]
        assert after.get(state_key) == sentinel, (
            f"{state_key}: the refused PATCH still mutated the stored value "
            f"({before.get(state_key)!r} -> {after.get(state_key)!r})")


def test_issues_off_does_not_disconnect_github(client):
    """#1924: the Issues off-toggle is an ENABLE flag, not a disconnect.

    Before #1924 the dashboard's off-toggle PATCHed ``github_connected=False``
    — a full GitHub disconnect that also killed the docs source and forced a
    fresh OAuth round-trip just to hide issues. The dashboard now PATCHes
    ``issues_enabled=False``; this pins the server-side invariant the fix
    rides on: turning the Issues source off leaves the CONNECTION and the
    sibling docs source intact (so re-enabling needs no re-authorization).
    """
    _make_sdk(namespace="registry")._get_registry().query(
        "CREATE (t:Team {id:$id, onboarding_state:$st})",
        params={"id": "test-team-1", "st": "{}"},
    )
    r = client.patch("/v1/onboarding/state", json={"github_connected": True})
    assert r.status_code == 200, r.text
    # the off-toggle's write: issues intent only — nothing about the connection
    r = client.patch("/v1/onboarding/state", json={"issues_enabled": False})
    assert r.status_code == 200, r.text
    body = r.json()["onboarding"]
    assert body["issues_enabled"] is False
    assert body["github_connected"] is True, \
        "turning the Issues source off must NOT disconnect GitHub (#1924)"
    assert body["docs_enabled"] is True, \
        "the sibling docs source must be untouched (#1924)"
    st = client.get("/v1/onboarding/state").json()["onboarding"]
    assert st["github_connected"] is True
    assert st["issues_enabled"] is False


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
    consent), but it IS get_current_org-gated (auth required — probes are
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
    claude-desktop / claude-web / cursor — harnesses with no install-probe
    beacon; cursor has a capture seam (#3819) but fires no probe) → 422 at the
    model boundary, never a silent drop (an unregistered key would be
    discarded by the allowlist filter and look like a recorded probe)."""
    from tortoise.hosted_api import _make_sdk
    _make_sdk(namespace="registry")._get_registry().query(
        "CREATE (t:Team {id:$id, onboarding_state:$st})",
        params={"id": "test-team-1", "st": "{}"},
    )
    r = client.post("/v1/sessions/install-probe",
                    json={"harness": "codex"})
    assert r.status_code == 422, r.text


def test_install_probe_requires_auth(unauth_client):
    """Task 14: the probe is get_current_org-gated — no auth, no probe."""
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
    # both server surfaces stay exactly the pinned 6-harness vocabulary.
    assert set(_HARNESS_ANALYTICS_VALUES) == {
        "claude", "claude-desktop", "claude-web", "codex", "cursor", "pi",
    }
    assert frozenset({
        "claude", "claude-desktop", "claude-web", "codex", "cursor", "pi",
    }) == _SESSION_HARNESS_VALUES

    # 2. the frontend harness set SUPERSETS the analytics vocab — and by
    # exactly the wizard-only chatgpt harness. #1701 R2: chatgpt is key-less
    # OAuth in the browser — ChatGPT never files sessions or fires copy
    # attribution (no local skills), so it is intentionally absent from the
    # server capture/analytics vocabulary. The wizard tab can therefore never
    # drift the server Literals; only a SECOND wizard-only harness would.
    root = Path(__file__).resolve().parent.parent
    harnesses_js = (root / "website/apps/dashboard/src/harnesses.js").read_text()
    m = re.search(r"export const HARNESS_ORDER\s*=\s*\[([^\]]*)\]", harnesses_js)
    assert m, "HARNESS_ORDER not found in website/apps/dashboard/src/harnesses.js"
    frontend = {s.strip().strip("'\"") for s in m.group(1).split(",") if s.strip()}
    assert set(_HARNESS_ANALYTICS_VALUES) <= frontend
    assert frontend - set(_HARNESS_ANALYTICS_VALUES) == {"chatgpt"}


# #3552: the last two key families the issue names beside the capture EVIDENCE
# set — the per-repo index cursor and the one-time legacy `-closed` backfill
# marker. Both have a legitimate SERVER writer, which is the issue's own
# precondition for making a key server-owned ("otherwise marking it server-owned
# bricks a client path"): the index walk stores the cursor via
# ``updates = {"github_index_cursor": cursors}`` and the backfill sets
# ``github_legacy_backfill_done=True``. Both are ALSO in the live PATCH model, so
# while they are unowned a normal authenticated client can rewind the cursor
# (re-walk, or SKIP issues) or resurrect the one-time backfill.
#
# Held as a literal set rather than derived from a registration table: unlike
# ``install_probe_*`` these are not minted per harness, so there is no source
# that could generate them, and a derived-only guard would iterate an empty set
# and stay vacuously green (the exact failure mode the sibling test documents).
_OPERATIONAL_SERVER_OWNED_KEYS: dict[str, str] = {
    "github_index_cursor": "github_index_cursor",
    "github_legacy_backfill_done": "github_legacy_backfill_done",
}


def test_operational_keys_not_client_writable(client):
    """#3552 residual — a client PATCH cannot rewind the index cursor or clear
    the one-time legacy backfill marker.

    Complements ``test_capture_verification_keys_not_client_writable``: that one
    covers the capture EVIDENCE family (receipts, per-harness last-errors,
    install probes); this covers the two OPERATIONAL keys #3552 names in the
    same breath, which the derivation there does not reach. Every key must be
    refused with 403 ``server_owned_key`` AND leave its stored value UNCHANGED —
    the value half is what makes this a real assertion, since a 403 that still
    mutated the row would satisfy a status-only check.
    """
    from tortoise.hosted_api import (
        _ALLOWED_STATE_KEYS,
        _PATCH_SERVER_OWNED_KEYS,
        _make_sdk,
        _update_onboarding_state,
    )
    from tortoise.hosted_api import (
        _OPERATIONAL_SERVER_OWNED_KEYS as _HA_OPERATIONAL_SERVER_OWNED_KEYS,
    )
    # Both directions, because the literal above keeps the loop non-vacuous but
    # cannot see the PRODUCTION set: dropping a key from production shrinks this
    # literal along with it, and adding one to production leaves this literal
    # untouched — either way the new key would get no refusal/value coverage.
    # The equality is what closes the drift in both directions.
    assert set(_OPERATIONAL_SERVER_OWNED_KEYS) == set(
        _HA_OPERATIONAL_SERVER_OWNED_KEYS), (
        "the test literal and hosted_api._OPERATIONAL_SERVER_OWNED_KEYS have "
        "drifted — an operational key on one side only is untested: "
        f"{sorted(set(_OPERATIONAL_SERVER_OWNED_KEYS) ^ set(_HA_OPERATIONAL_SERVER_OWNED_KEYS))}")
    # Provision the Team node so the assertions below read REAL persisted state
    # (the state writer is MERGE...SET — a silent no-op without the node).
    _make_sdk(namespace="registry")._get_registry().query(
        "CREATE (t:Team {id:$id, onboarding_state:$st})",
        params={"id": "test-team-1", "st": "{}"},
    )
    for state_key, patch_field in sorted(_OPERATIONAL_SERVER_OWNED_KEYS.items()):
        assert state_key in _ALLOWED_STATE_KEYS, (
            f"{state_key} is not a registered state key — the refusal below "
            "would be vacuous")
        # THE HAZARD, asserted first so a regression fails on the cause and not
        # on a downstream KeyError.
        assert state_key in _PATCH_SERVER_OWNED_KEYS, (
            f"{state_key} is client-writable: an authenticated PATCH can set "
            "it with no server action (rewind the index cursor / re-run the "
            "one-time backfill) — #3552")
        # A value DISTINCT from the sample the client sends, so "unchanged" is a
        # real comparison rather than two identical writes.
        sentinel: object = (
            {"repo-a": {"updated_at": "2020-01-01T00:00:00Z", "number": 1}}
            if state_key == "github_index_cursor" else True)
        sample: object = (
            {"repo-b": {"updated_at": "2026-08-25T00:00:00Z", "number": 9}}
            if state_key == "github_index_cursor" else False)
        assert sample != sentinel
        # The TRUSTED path writes it — this is what a client must not reach.
        _update_onboarding_state(
            "test-team-1", _echo=False, **{state_key: sentinel})
        before = client.get("/v1/onboarding/state").json()["onboarding"]
        assert before.get(state_key) == sentinel, (
            f"{state_key}: the server-side write did not land "
            f"({before.get(state_key)!r}) — the refusal below would be vacuous "
            "against a value that was never there")
        r = client.patch("/v1/onboarding/state", json={patch_field: sample})
        assert r.status_code == 403, (
            f"server-owned key {state_key} was client-writable: {r.text}")
        assert r.json()["detail"] == {
            "message": "server_owned_key", "keys": [state_key]}, r.text
        after = client.get("/v1/onboarding/state").json()["onboarding"]
        assert after.get(state_key) == before.get(state_key), (
            f"{state_key} CHANGED despite the 403: "
            f"{before.get(state_key)!r} -> {after.get(state_key)!r}")
