"""#2004 (W8) builder capability catalog endpoint — docker-lane tests.

GET /v1/capabilities (epic I-7) returns the registry-backed indexers+
extractors catalog. #3913 (owner ruling 2026-09-20): the build-fork
completion gate is the two OBSERVED acts — harness-connected +
first-points-filed — never a catalog render; the catalog-presented step edge
stays an accepted, optional record (the agent/external checkpoint and the
PATCH path still MERGE it; no dashboard path writes it since #3913).

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
    """A freshly registered team (registry lane) → (org_id, email)."""
    email = f"w8c-{uuid.uuid4().hex[:10]}@example.com"
    r = tc.post("/v1/register", json={"email": email, "password": "password123"})
    assert r.status_code == 200, r.text
    tc.headers.update({"Authorization": f"Bearer {r.json()['api_key']}"})
    return r.json()["org_id"], email


def _checkpoint(tc, **body) -> dict:
    r = tc.post("/v1/onboarding/state/checkpoint", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _completed(org_id: str) -> set[str]:
    return set(onboarding_state.completed_steps(_make_sdk(namespace=org_id)._get_proj(), org_id))


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
    """#3913: the build-fork gate is the two OBSERVED acts (harness-connected +
    first-points-filed) — decide/catalog complete nothing. The catalog-presented
    id stays accepted and replay-safe."""

    def _build_fork_org(self, client) -> tuple[str, str]:
        """Fork=build + BOTH observed acts → gate-satisfied org."""
        org_id, _email = _registered(client)
        _checkpoint(client, fork="build")
        _checkpoint(client, step="harness-connected")
        _checkpoint(client, step="first-points-filed")
        return org_id, _email

    def test_build_fork_completes_on_the_two_observed_acts(self, client):
        """fork=build: harness-connected alone stays active (fail-closed);
        adding first-points-filed completes it — no catalog-presented needed."""
        org_id, _ = _registered(client)
        _checkpoint(client, fork="build")
        r = _checkpoint(client, step="harness-connected")
        assert r["onboarding"]["status"] == "active", r  # only ONE observed act
        r2 = _checkpoint(client, step="first-points-filed")
        assert r2["onboarding"]["status"] == "complete", r2
        assert "first-points-filed" in r2["created_steps"]
        # #3913: the catalog step was never required and was never written here.
        assert "catalog-presented" not in _completed(org_id)

    def test_build_decide_alone_never_completes(self, client):
        """decide-completed is a SELF-fork row — it can never complete build."""
        _org_id, _ = _registered(client)
        _checkpoint(client, fork="build")
        r = _checkpoint(client, step="decide-completed")
        assert r["onboarding"]["status"] == "active", r

    def test_catalog_presented_replay_is_noop(self, client):
        """#3913: catalog-presented is no longer required, but the id stays
        accepted — recording it is a 200 and a replay is a keyed-MERGE no-op
        (never regresses or double-fires)."""
        _org_id, _ = self._build_fork_org(client)
        first = _checkpoint(client, step="catalog-presented")
        assert first["onboarding"]["status"] == "complete"
        assert "catalog-presented" in first["created_steps"]
        replay = _checkpoint(client, step="catalog-presented")
        assert replay["onboarding"]["status"] == "complete"
        assert "catalog-presented" in replay["noop_steps"]

    def test_patch_catalog_presented_marks_the_optional_edge(self, client):
        """The PATCH surface (`catalog_presented: true`) still MERGEs the SAME
        step edge — recording only, never a completion gate. The org here is
        already completed by the two observed acts, so the meaningful assertion
        is that the edge MERGED (the fail-closed arm — the PATCH alone on a
        fresh build org — is
        test_catalog_presented_alone_never_completes_a_build_org)."""
        org_id, _ = self._build_fork_org(client)
        r = client.patch("/v1/onboarding/state", json={"catalog_presented": True})
        assert r.status_code == 200, r.text
        # The edge MERGE is the meaningful assertion here (the fail-closed arm —
        # the PATCH alone on a fresh build org — is
        # test_catalog_presented_alone_never_completes_a_build_org).
        # No `status == "complete"` assert: this org was already completed by
        # `_build_fork_org`, so it would restate the fixture, not test the app.
        assert "catalog-presented" in _completed(org_id)

    def test_catalog_presented_alone_never_completes_a_build_org(self, client):
        """Fail-closed guard (NOT a #3913 pin — it behaves the same on both
        sides of the ruling): a FRESH build org that PATCHes
        `catalog_presented: true` and nothing else stays ACTIVE. The catalog id
        is an accepted record, never sufficient on its own. The test that
        DISTINGUISHES the gate change is
        test_build_fork_completes_on_the_two_observed_acts above."""
        org_id, _ = _registered(client)
        _checkpoint(client, fork="build")
        r = client.patch("/v1/onboarding/state", json={"catalog_presented": True})
        assert r.status_code == 200, r.text
        assert r.json()["onboarding"]["status"] == "active", r.text
        assert r.json()["onboarding"]["onboarding_complete"] is False, r.text
        # the record WAS merged — it is simply not a completion input
        assert "catalog-presented" in _completed(org_id)
