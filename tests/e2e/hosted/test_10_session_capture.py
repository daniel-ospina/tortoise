"""E2E-10-D — agent session capture (LLM-default extraction).

Reconstructed case (#303). POST /v1/sessions with a dense conversation →
session stored and retrievable; the M2 LLM extractor (#822 — the regex mode
knob is removed; TORTOISE_SESSION_LLM_MOCK=1 installs the offline MockModel
on the E2E server) turns conversation sentences into Points.

Negatives: turn cap (MAX_SESSION_TURNS=500) → 400; oversized turn content
(>5000 chars) → accepted + truncated to the stored window (the old 422 is
removed — #1532 D1, SDK truncation parity); unauthenticated → 401; a team
that disabled session_recording (default is ON — #1927) → 409
state-conflict.

Consent: #1927 removed the ENFORCED capture consent — session_recording is
now DEFAULT-ON (ToS-covered) with an optional off-switch: a fresh team
(session_recording unset) captures on POST /v1/sessions with no gate; a team
that disables it (PATCH /v1/onboarding/state, the dashboard's Memory sources
> Agent sessions toggle) gets a clear 409. The suite seeds the flag
per-tenant to stay explicit about the exercised path.
"""
from __future__ import annotations

import uuid

import pytest
from conftest import is_remote_mode, skip_unless_hosted_e2e

skip_unless_hosted_e2e()


def _enable_session_recording(api, tenant: dict) -> None:
    """Seed the tenant's session_recording flag (explicit for determinism;
    the default is ON — #1927)."""
    h = {"Authorization": f"Bearer {tenant['api_key']}"}
    # playwright-python's APIRequestContext takes `data` (dict → JSON body),
    # not `json` (#1928 — #1892's review-fix commit 24126090 swapped the
    # original valid `data` kwarg for `json`, which TypeError'd before the
    # request ever reached the consent gate).
    r = api.patch("/v1/onboarding/state", headers=h,
                  data={"session_recording": True})
    assert r.status == 200, f"enable session_recording: {r.status} {r.text()}"
    # Self-verifying: the seed must actually stick — a silently dropped
    # PATCH would 409 the capture POSTs below (loud), but asserting the
    # echoed state here makes the off-switch state explicit.
    assert r.json()["onboarding"]["session_recording"] is True, r.text()


def _dense_conversation(n_turns: int = 6) -> list[dict]:
    turns = []
    for i in range(n_turns):
        turns.append({"role": "user",
                      "content": f"Turn {i}: we should migrate the graph engine. "
                                 f"Decision: use FalkorDB for tenant {i}."})
        turns.append({"role": "assistant",
                      "content": f"Acknowledged. The plan is to isolate namespaces "
                                 f"per team ({i}). Next steps: provision keys."})
    return turns


def test_session_capture_and_extraction(api, tenant_factory):
    """Positive: capture succeeds, session lists, and extraction produced
    Points in the team graph (regex baseline)."""
    t = tenant_factory("session")
    _enable_session_recording(api, t)
    h = {"Authorization": f"Bearer {t['api_key']}"}
    sid = f"sess-e2e10-{uuid.uuid4().hex[:8]}"

    before = api.get("/v1/points", headers=h).json()["points"]
    r = api.post("/v1/sessions", headers=h,
                 data={"conversation": _dense_conversation(), "session_id": sid})
    assert r.status == 200, f"session capture: {r.status} {r.text()}"

    r = api.get("/v1/sessions", headers=h)
    assert r.status == 200, r.text()
    assert any(s.get("session_id") == sid or s.get("id") == sid
               for s in r.json()["sessions"]), \
        f"captured session missing: {r.text()}"

    r = api.get(f"/v1/sessions/{sid}", headers=h)
    assert r.status == 200, f"session detail: {r.status} {r.text()}"

    after = api.get("/v1/points", headers=h).json()["points"]
    assert len(after) > len(before), \
        "LLM extraction must turn dense conversation into Points"


def test_session_turn_cap_400(api, tenant_factory):
    t = tenant_factory("session-cap")
    _enable_session_recording(api, t)
    h = {"Authorization": f"Bearer {t['api_key']}"}
    too_many = [{"role": "user", "content": f"t{i}"} for i in range(501)]
    r = api.post("/v1/sessions", headers=h, data={"conversation": too_many})
    assert r.status == 400, f"501 turns must 400 (cap 500), got {r.status}"
    assert "cap" in r.text().lower() or "turn" in r.text().lower(), r.text()


def test_session_oversized_turn_truncates(api, tenant_factory):
    """#1532 D1 (contract change, flagged): oversized turn content (>5000
    chars) is accepted and truncated to the stored window — the old 422 is
    removed (SDK truncation parity)."""
    t = tenant_factory("session-big")
    _enable_session_recording(api, t)
    h = {"Authorization": f"Bearer {t['api_key']}"}
    sid = f"sess-e2e10-big-{uuid.uuid4().hex[:8]}"
    r = api.post("/v1/sessions", headers=h,
                 data={"conversation": [{"role": "user", "content": "x" * 5001}],
                       "session_id": sid})
    assert r.status == 200, f"oversized turn must truncate to 200, got {r.status} {r.text()}"

    # #1928 review P2: don't pass the acceptance half vacuously — verify the
    # truncation half too: the stored turn is windowed to 5000 chars (the
    # detail endpoint strips the "[user] " prefix), so the 5001-char body
    # cannot come back whole.
    r = api.get(f"/v1/sessions/{sid}", headers=h)
    assert r.status == 200, f"session detail: {r.status} {r.text()}"
    turns = r.json()["turn_points"]
    user_turn = next((tn for tn in turns if tn.get("role") == "user"), None)
    assert user_turn is not None, f"user turn missing: {r.text()}"
    assert len(user_turn["content"]) == 5000, (
        f"oversized turn must truncate to the 5000-char window, "
        f"got {len(user_turn['content'])} chars")


@pytest.mark.skipif(is_remote_mode(), reason="gate test needs a fresh tenant (remote pool shares 3)")
def test_session_recording_gate_409_on_disabled(api, tenant_factory):
    """#1927 consent rework (merged from main): session_recording is DEFAULT-ON
    (ToS-covered, opt-out) — the old #1727 enforced 403 consent gate is gone.
    A fresh team that never touched the flag captures with NO gate; a team
    that explicitly disables it (dashboard off-switch) gets a clear 409
    state-conflict on POST /v1/sessions."""
    t = tenant_factory("session-noconsent")
    h = {"Authorization": f"Bearer {t['api_key']}"}
    # Fresh tenant, flag unset → default ON → no gate: capture succeeds.
    r = api.post("/v1/sessions", headers=h,
                 data={"conversation": [{"role": "user",
                                          "content": "ship the migration this week"}]})
    assert r.status == 200, f"default-on team must capture with no gate, got {r.status} {r.text()}"

    # Explicitly disable (dashboard Memory sources > Agent sessions toggle).
    r = api.patch("/v1/onboarding/state", headers=h,
                  data={"session_recording": False})
    assert r.status == 200, f"disable session_recording: {r.status} {r.text()}"
    assert r.json()["onboarding"]["session_recording"] is False, r.text()

    # Disabled → clear 409 state-conflict (NOT the old 403 consent error).
    r = api.post("/v1/sessions", headers=h,
                 data={"conversation": [{"role": "user",
                                          "content": "ship the migration this week"}]})
    assert r.status == 409, f"disabled team must 409, got {r.status} {r.text()}"
    assert "disabled" in r.text().lower(), r.text()


def test_session_unauthenticated_401(api):
    r = api.post("/v1/sessions", data={"conversation": [{"role": "user", "content": "hi"}]})
    assert r.status == 401
