"""E2E-10-D — agent session capture (LLM-default extraction).

Reconstructed case (#303). POST /v1/sessions with a dense conversation →
session stored and retrievable; the M2 LLM extractor (#822 — the regex mode
knob is removed; TORTOISE_SESSION_LLM_MOCK=1 installs the offline MockModel
on the E2E server) turns conversation sentences into Points.

Negatives: turn cap (MAX_SESSION_TURNS=500) → 400; oversized turn content
(>5000 chars) → 422; unauthenticated → 401.
"""
from __future__ import annotations

import uuid

from conftest import skip_unless_hosted_e2e

skip_unless_hosted_e2e()


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
    h = {"Authorization": f"Bearer {t['api_key']}"}
    too_many = [{"role": "user", "content": f"t{i}"} for i in range(501)]
    r = api.post("/v1/sessions", headers=h, data={"conversation": too_many})
    assert r.status == 400, f"501 turns must 400 (cap 500), got {r.status}"
    assert "cap" in r.text().lower() or "turn" in r.text().lower(), r.text()


def test_session_oversized_turn_422(api, tenant_factory):
    t = tenant_factory("session-big")
    h = {"Authorization": f"Bearer {t['api_key']}"}
    r = api.post("/v1/sessions", headers=h,
                 data={"conversation": [{"role": "user", "content": "x" * 5001}]})
    assert r.status == 422, f"oversized turn must 422, got {r.status}"


def test_session_unauthenticated_401(api):
    r = api.post("/v1/sessions", data={"conversation": [{"role": "user", "content": "hi"}]})
    assert r.status == 401
