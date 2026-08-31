"""E2E-12 — cross-W full onboarding journey slice (#2001 W5, epic #1976).

Boots the REAL deployment artifact (`uvicorn tortoise.hosted_api:app`,
embedded FalkorDBLite, registry control plane) and drives the journey over
real HTTP — the DOCKER-LANE leg owns the direct graph assertions (node
version=1, completed_steps match, onboards edge); THIS leg is HTTP-only
(substrate single-writer constraint: the pytest process never opens the
server's DB file).

Covers:
  1. signup → org → fork → connect → seed → decide one sitting (mock agent
     via checkpoint calls + merged GET states) → gate-complete wire.
  2. Dismissal alone never completes (no decide edge).
  3. build fork: decide does NOT complete; catalog-presented does.
  4. Grandfathered wire stability (DE2E-6): legacy wizard PATCH completes →
     wire true (guard); the FIRST agent step edge flips control to the node.
  5. Checkpoint created/noop signals (the W11 surface; event emission is
     #2006's DE2E-12 target).

Gate: RUN_HOSTED_E2E=1 (local hermetic server, #303 convention).
"""
from __future__ import annotations

import uuid

from conftest import skip_unless_hosted_e2e  # type: ignore

skip_unless_hosted_e2e()


def _register(api, tag: str) -> tuple[str, dict]:
    """Register a fresh org; returns (team_id, auth headers)."""
    email = f"w5-{tag}-{uuid.uuid4().hex[:8]}@e2e.premise-labs.dev"
    r = api.post("/v1/register", data={"email": email, "password": "E2ePass-303-x"})
    assert r.status == 200, r.text()
    body = r.json()
    headers = {"Authorization": f"Bearer {body['api_key']}"}
    return body["team_id"], headers


def _get_state(api, headers: dict) -> dict:
    r = api.get("/v1/onboarding/state", headers=headers)
    assert r.status == 200, r.text()
    return r.json()["onboarding"]


def _checkpoint(api, headers: dict, payload: dict):
    r = api.post("/v1/onboarding/state/checkpoint",
                 headers=headers, data=payload)
    return r


def test_full_self_journey_one_sitting(api):
    """DE2E-12: signup → org → fork → connect → seed → decide. The eager
    node exists at first read (version=1, team-named auto-satisfied);
    fork set-once; step edges keyed-MERGE with honest created/noop signals;
    the fork-aware gate completes the org (status + wire)."""
    _team_id, headers = _register(api, "self")
    # first read: eager-init node (registry lane init in the SAME statement
    # as TeamMeta) — FLOW keys present, operational keys present
    st = _get_state(api, headers)
    assert st["version"] == 1
    assert st["status"] == "active"
    assert st["fork"] is None          # first org → fork card asked once
    assert st["compact"] is False
    assert "team-named" in st["completed_steps"]
    assert st["onboarding_complete"] is False
    assert "github_connected" in st    # operational jsonb keys still served

    # fork card (set-once)
    r = _checkpoint(api, headers, {"fork": "self"})
    assert r.status == 200, r.text()
    assert r.json()["onboarding"]["fork"] == "self"
    # replay same → 200; changed → 409
    r = _checkpoint(api, headers, {"fork": "self"})
    assert r.status == 200
    r = _checkpoint(api, headers, {"fork": "build"})
    assert r.status == 409

    # connect (harness-connected) — created signal, then noop replay
    r = _checkpoint(api, headers, {"step": "harness-connected"})
    assert r.status == 200
    assert r.json()["created_steps"] == ["harness-connected"]
    assert r.json()["noop_steps"] == []
    r = _checkpoint(api, headers, {"step": "harness-connected"})
    assert r.json()["created_steps"] == []
    assert r.json()["noop_steps"] == ["harness-connected"]

    # seed (first-points-filed) + decide — gate satisfied → complete
    _checkpoint(api, headers, {"step": "first-points-filed"})
    r = _checkpoint(api, headers, {"step": "decide-completed"})
    assert r.status == 200, r.text()
    st = r.json()["onboarding"]
    assert st["status"] == "complete"
    assert st["onboarding_complete"] is True
    assert st["version"] == 1
    # monotonic: a later noop write cannot regress the wire
    st = _get_state(api, headers)
    assert st["status"] == "complete"
    assert st["onboarding_complete"] is True
    # completed_steps are the canonical edge projection
    assert {"team-named", "harness-connected", "first-points-filed",
            "decide-completed"} <= set(st["completed_steps"])


def test_dismissal_alone_never_completes(api):
    _team_id, headers = _register(api, "dismiss")
    _checkpoint(api, headers, {"fork": "self"})
    _checkpoint(api, headers, {"step": "harness-connected"})
    _checkpoint(api, headers, {"step": "first-points-filed"})
    r = _checkpoint(api, headers, {"last_decide_attempt": "dismissed"})
    assert r.status == 200
    st = r.json()["onboarding"]
    assert st["status"] == "active"
    assert st["onboarding_complete"] is False
    assert "decide-completed" not in st["completed_steps"]
    assert st["last_decide_attempt"] == "dismissed"


def test_build_fork_uses_catalog_not_decide(api):
    _team_id, headers = _register(api, "build")
    _checkpoint(api, headers, {"fork": "build"})
    _checkpoint(api, headers, {"step": "harness-connected"})
    _checkpoint(api, headers, {"step": "first-points-filed"})
    # decide does NOT complete a build org (catalog is the build gate step)
    r = _checkpoint(api, headers, {"step": "decide-completed"})
    assert r.json()["onboarding"]["status"] == "active"
    # catalog-presented (dashboard PATCH surface) completes it
    r = api.patch("/v1/onboarding/state", headers=headers,
                  data={"catalog_presented": True})
    assert r.status == 200, r.text()
    assert r.json()["onboarding"]["status"] == "complete"
    assert r.json()["onboarding"]["onboarding_complete"] is True


def test_grandfathered_wire_stable_then_node_governs(api):
    """DE2E-6: a legacy-wizard completer's wire stays true (guard); the FIRST
    agent step edge flips control to the node (documented one-way door)."""
    _team_id, headers = _register(api, "gf")
    # legacy wizard completion (the carve-out jsonb write — still active
    # until W1 removes wizardComplete)
    r = api.patch("/v1/onboarding/state", headers=headers,
                  data={"onboarding_complete": True})
    assert r.status == 200, r.text()
    st = r.json()["onboarding"]
    assert st["onboarding_complete"] is True      # guard: node active, zero agent edges
    assert st["status"] == "active"
    # the agent engages → the FIRST step edge flips control to the node
    r = _checkpoint(api, headers, {"step": "harness-connected"})
    st = r.json()["onboarding"]
    assert st["onboarding_complete"] is False
    assert st["status"] == "active"


def test_unknown_step_and_extra_rejected(api):
    _team_id, headers = _register(api, "neg")
    r = _checkpoint(api, headers, {"step": "bogus-step"})
    assert r.status == 422
    r = _checkpoint(api, headers, {"step": "capture-disclosed", "fork": "self"})
    assert r.status == 400
    r = _checkpoint(api, headers, {"status": "complete"})
    assert r.status == 403
