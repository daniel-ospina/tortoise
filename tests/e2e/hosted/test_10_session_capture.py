"""E2E-10-D — agent session capture (LLM-default extraction).

Reconstructed case (#303). POST /v1/sessions with a dense conversation →
session stored and retrievable; the M2 LLM extractor (#822 — the regex mode
knob is removed; TORTOISE_SESSION_LLM_MOCK=1 installs the offline MockModel
on the E2E server) turns conversation sentences into Points.

Negatives: turn cap (MAX_SESSION_TURNS=500) → 400; oversized turn content
(>5000 chars) → accepted + truncated to the stored window (the old 422 is
removed — #1532 D1, SDK truncation parity); unauthenticated → 401.

Consent: #1727 Task 11 (P0) made session_recording the ENFORCED capture
consent — a fresh team (session_recording=False) gets 403 on POST
/v1/sessions until it opts in via the public PATCH /v1/onboarding/state
surface (the same call the dashboard's Memory sources > Agent sessions
toggle makes). The suite opts each tenant in before exercising capture.
"""
from __future__ import annotations

import uuid

from conftest import skip_unless_hosted_e2e

skip_unless_hosted_e2e()


def _enable_session_recording(api, tenant: dict) -> None:
    """Opt the tenant into session capture (the #1727 enforced consent)."""
    h = {"Authorization": f"Bearer {tenant['api_key']}"}
    r = api.patch("/v1/onboarding/state", headers=h,
                  json={"session_recording": True})
    assert r.status == 200, f"enable session_recording: {r.status} {r.text()}"
    # Self-verifying: the opt-in must actually stick — a silently dropped
    # PATCH would 403 the capture POSTs below (loud), but asserting the
    # echoed state here makes the consent precondition explicit.
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
    r = api.post("/v1/sessions", headers=h,
                 data={"conversation": [{"role": "user", "content": "x" * 5001}]})
    assert r.status == 200, f"oversized turn must truncate to 200, got {r.status} {r.text()}"


def test_session_unauthenticated_401(api):
    r = api.post("/v1/sessions", data={"conversation": [{"role": "user", "content": "hi"}]})
    assert r.status == 401
