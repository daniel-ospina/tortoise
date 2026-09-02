"""#2000 (W4) Settings-surface toggle persistence — docker-lane integration tests.

W4 owns the org Settings tab (7th tab): the four memory-source toggles
(github_connected / github_indexed / github_docs_indexed / session_recording)
are reachable ONLY via Settings → Memory sources (DE2E-2). This module pins
the server leg of that contract (surface 10):

- the four operational keys round-trip through PATCH /v1/onboarding/state
  and persist across re-reads (toggle persistence);
- they are JSONB-side keys: never FLOW keys, never step edges, never on the
  OnboardingState graph node — the checkpoint surface (graph-only) can never
  write them, so the only surface that touches them is the state surface the
  Settings panel reads/writes;
- session_recording defaults ON (#1927) and flips off/back.

Runs in the docker lane (TORTOISE_DB_URI) — mirrors test_onboarding_state_split.py.
URI-less runs (tier-2 embedded legs, carve-out) SKIP at module level.
"""

from __future__ import annotations

import os
import uuid

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("TORTOISE_ENCRYPTION_KEY", "I2n-E3K857hF9ENLgrOZ8YBPkEB4tu4jyrb1aJMUtnI=")

import pytest

# docker-lane gate (epic #1647 P4 / #1997): URI-less embedded legs cannot run
# these server-mode assertions — skip cleanly instead of failing.
from tortoise.config import is_db_uri as _is_db_uri

if not _is_db_uri(os.environ.get("TORTOISE_DB_URI")):
    pytest.skip(
        "docker-lane W4 settings tests require TORTOISE_DB_URI (tier-2 embedded legs skip)",
        allow_module_level=True,
    )

from fastapi.testclient import TestClient

from tortoise.hosted_api import (
    _ALLOWED_STATE_KEYS,
    _get_onboarding_state,
    _make_sdk,
    app,
)
from tortoise.onboarding import state as onboarding_state

# The four Settings → Memory sources toggles (DE2E-2 / epic plan P3).
MEMORY_SOURCE_KEYS = (
    "github_connected",
    "github_indexed",
    "github_docs_indexed",
    "session_recording",
)


def _registered_client():
    """TestClient + a freshly registered team's key (registry lane)."""
    tc = TestClient(app)
    tc.__enter__()
    email = f"w4-{uuid.uuid4().hex[:10]}@example.com"
    r = tc.post("/v1/register", json={"email": email, "password": "password123"})
    assert r.status_code == 200, r.text
    tc.headers.update({"Authorization": f"Bearer {r.json()['api_key']}"})
    return tc, r.json()["team_id"]


class TestToggleRegistration:
    def test_memory_source_keys_are_operational_not_flow(self):
        """The four toggles are jsonb-side keys — never FLOW keys, never
        step ids (the graph checkpoint vocabulary). They are reachable only
        via the state surface the Settings panel uses."""
        for key in MEMORY_SOURCE_KEYS:
            assert key not in onboarding_state.FLOW_KEYS, f"{key} must stay jsonb-side"
            assert key not in onboarding_state.STEP_IDS, f"{key} is not a step edge"
        # and they ARE registered in the jsonb PATCH allowlist (else the
        # allowlist filter silently drops them — STATE-KEY REGISTRATION).
        for key in MEMORY_SOURCE_KEYS:
            assert key in _ALLOWED_STATE_KEYS, f"{key} must be registered in the allowlist"


class TestTogglePersistence:
    def test_four_toggles_round_trip_and_persist(self):
        """PATCH each of the four keys, then GET — the written values must
        persist (multi-round writes, readback through the merged projection)."""
        tc, team_id = _registered_client()
        try:
            writes = [
                {"github_connected": True},
                {"github_indexed": True},
                {"github_docs_indexed": True},
                {"session_recording": False},
            ]
            for w in writes:
                r = tc.patch("/v1/onboarding/state", json=w)
                assert r.status_code == 200, r.text
                echo = r.json()["onboarding"]
                for k, v in w.items():
                    assert echo.get(k) is v, f"{k} echo wrong: {echo.get(k)}"
            # persisted across a fresh GET
            r = tc.get("/v1/onboarding/state")
            assert r.status_code == 200, r.text
            st = r.json()["onboarding"]
            for k, v in {
                "github_connected": True,
                "github_indexed": True,
                "github_docs_indexed": True,
                "session_recording": False,
            }.items():
                assert st.get(k) is v, f"{k} did not persist: {st.get(k)}"
            # raw jsonb store agrees (operational keys live jsonb-side)
            raw = _get_onboarding_state(team_id)
            assert raw.get("github_connected") is True
            assert raw.get("session_recording") is False
        finally:
            tc.__exit__(None, None, None)

    def test_session_recording_defaults_on_and_flips(self):
        """#1927: session_recording is the DEFAULT-ON off-switch (ToS-
        covered, never a consent gate) — flips off and back without a re-ask."""
        tc, _team_id = _registered_client()
        try:
            r = tc.get("/v1/onboarding/state")
            assert r.json()["onboarding"].get("session_recording") is not False, (
                "session_recording must default ON"
            )
            r = tc.patch("/v1/onboarding/state", json={"session_recording": False})
            assert r.json()["onboarding"]["session_recording"] is False
            r = tc.patch("/v1/onboarding/state", json={"session_recording": True})
            assert r.json()["onboarding"]["session_recording"] is True
        finally:
            tc.__exit__(None, None, None)

    def test_toggles_never_land_on_the_graph_node(self):
        """The four operational keys must never be written to the
        OnboardingState node (store SPLIT, DM-2 — graph holds FLOW state;
        jsonb keeps OPERATIONAL keys)."""
        tc, team_id = _registered_client()
        try:
            for w in [
                {"github_connected": True},
                {"session_recording": False},
                {"github_docs_indexed": True},
                {"github_indexed": True},
            ]:
                assert tc.patch("/v1/onboarding/state", json=w).status_code == 200
            node = onboarding_state.read_onboarding_node(
                _make_sdk(namespace=team_id)._get_proj(), team_id
            )
            assert node is not None
            for k in MEMORY_SOURCE_KEYS:
                assert k not in node, f"{k} leaked onto the graph node"
        finally:
            tc.__exit__(None, None, None)

    def test_checkpoint_surface_cannot_write_toggles(self):
        """DE2E-2 server leg: the graph checkpoint surface (steps/fork/
        compact) is not a route to the memory-source toggles — posting a
        toggle key there must NOT land in jsonb (it is not a checkpoint
        field; the only surface is the state PATCH the Settings panel uses)."""
        tc, _team_id = _registered_client()
        try:
            for key in MEMORY_SOURCE_KEYS:
                r = tc.post("/v1/onboarding/state/checkpoint", json={key: True})
                # extra="forbid" on the checkpoint model → 422: the toggle
                # keys are not FLOW operations and can never route through
                # the graph checkpoint surface (reachability leak guard).
                assert r.status_code == 422, (
                    f"{key} routed through checkpoint: {r.status_code} {r.text}"
                )
        finally:
            tc.__exit__(None, None, None)
