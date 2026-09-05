"""#2004 (W8) builder capability catalog endpoint — docker-lane tests.

GET /v1/capabilities (epic I-7) returns the registry-backed indexers+
extractors catalog; the build-fork completion gate stays evaluable through
the catalog-presented step edge (the dashboard write path PATCHes
catalog_presented, agents checkpoint the step — both MERGE the same edge).

Runs in the docker lane (TORTOISE_DB_URI) — the gate assertions exercise
real FalkorDB step-edge writes. URI-less runs (tier-2 embedded legs,
carve-out) SKIP at module level — mirror of
test_onboarding_state_split.py's guard.
"""

from __future__ import annotations

import os
import uuid

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("TORTOISE_ENCRYPTION_KEY", "I2n-E3K857hF9ENLgrOZ8YBPkEB4tu4jyrb1aJMUtnI=")

import pytest

from tortoise.config import is_db_uri as _is_db_uri

if not _is_db_uri(os.environ.get("TORTOISE_DB_URI")):
    pytest.skip(
        "docker-lane capabilities tests require TORTOISE_DB_URI (tier-2 embedded legs skip)",
        allow_module_level=True,
    )

from fastapi.testclient import TestClient

from tortoise.hosted_api import _make_sdk, app
from tortoise.onboarding import state as onboarding_state


@pytest.fixture
def client():
    with TestClient(app) as tc:
        yield tc


def _registered(tc) -> tuple[str, str]:
    """A freshly registered team (registry lane) → (team_id, email)."""
    email = f"w8c-{uuid.uuid4().hex[:10]}@example.com"
    r = tc.post("/v1/register", json={"email": email, "password": "password123"})
    assert r.status_code == 200, r.text
    tc.headers.update({"Authorization": f"Bearer {r.json()['api_key']}"})
    return r.json()["team_id"], email


def _checkpoint(tc, **body) -> dict:
    r = tc.post("/v1/onboarding/state/checkpoint", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _completed(team_id: str) -> set[str]:
    return set(onboarding_state.completed_steps(_make_sdk(namespace=team_id)._get_proj(), team_id))


class TestCapabilitiesEndpoint:
    def test_endpoint_returns_accurate_catalog(self, client):
        """I-7 contract: 200 {modules: [...]} with the canonical indexers+
        extractors (DE2E-9's 3-module minimum + the future extractor)."""
        _registered(client)
        r = client.get("/v1/capabilities")
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body) == {"modules"}
        rows = body["modules"]
        names = [m["name"] for m in rows]
        # accurate + pullable: the presented set + the future module
        assert "Session recorder" in names
        assert "Session extractor" in names
        assert "Document indexer" in names
        assert "Document extractor" in names
        assert len(rows) == 4
        for m in rows:
            assert m["kind"] in ("indexer", "extractor")
            assert m["description"]
            assert isinstance(m["available"], bool)
        future = next(m for m in rows if m["name"] == "Document extractor")
        assert future["available"] is False

    def test_endpoint_matches_registry_rows(self, client):
        """Endpoint payload == registry accessor (single source — no drift)."""
        from tortoise.tool_registry import capability_catalog

        _registered(client)
        r = client.get("/v1/capabilities")
        assert r.status_code == 200
        assert r.json()["modules"] == capability_catalog()

    def test_endpoint_requires_auth(self, client):
        """Dual-auth surface: an unauthenticated call is rejected (401)."""
        r = client.get("/v1/capabilities")
        assert r.status_code == 401


class TestBuildForkGate:
    """The catalog-presented step edge keeps the build-fork gate evaluable
    (surface 13 + DE2E-12: build completes WITHOUT decide — W8 keeps the
    W1/W5 mechanism; the endpoint swap is source-only)."""

    def _build_fork_active_org(self, client) -> tuple[str, str]:
        team_id, _email = _registered(client)
        _checkpoint(client, fork="build")
        _checkpoint(client, step="harness-connected")
        _checkpoint(client, step="first-points-filed")
        return team_id, _email

    def test_catalog_presented_completes_build_fork_without_decide(self, client):
        """build = harness-connected + first-points-filed + catalog-presented
        → status complete; decide-completed alone can NEVER complete it."""
        team_id, _ = self._build_fork_active_org(client)
        # decide-completed alone: still active (decide is NOT the build gate)
        r = _checkpoint(client, step="decide-completed")
        assert r["onboarding"]["status"] == "active"
        assert "catalog-presented" not in _completed(team_id)
        # catalog-presented (checkpoint agent path) → complete
        r2 = _checkpoint(client, step="catalog-presented")
        assert r2["onboarding"]["status"] == "complete", r2
        assert "catalog-presented" in r2["created_steps"]

    def test_catalog_presented_replay_is_noop(self, client):
        """Re-presenting the catalog after completion → 200 idempotent
        no-op (keyed-MERGE — the once-per-org catalog-presented edge never
        regresses or double-fires)."""
        _team_id, _ = self._build_fork_active_org(client)
        first = _checkpoint(client, step="catalog-presented")
        assert first["onboarding"]["status"] == "complete"
        replay = _checkpoint(client, step="catalog-presented")
        assert replay["onboarding"]["status"] == "complete"
        assert "catalog-presented" in replay["noop_steps"]

    def test_dashboard_patch_catalog_presented_completes_gate(self, client):
        """The dashboard write surface (PATCH catalog_presented: true) marks
        the SAME step edge → the build gate completes for the browser path."""
        _team_id, _ = self._build_fork_active_org(client)
        r = client.patch("/v1/onboarding/state", json={"catalog_presented": True})
        assert r.status_code == 200, r.text
        assert r.json()["onboarding"]["status"] == "complete", r.text
