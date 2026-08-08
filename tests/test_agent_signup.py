"""Zero-email signup tests (issue #663).

POST /v1/agent/signup — anonymous device mints a team + key, no email/
dashboard/Supabase account. Per-identity rate limit (3/hour).
"""
from __future__ import annotations

import os
import sys
import uuid

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from tortoise.hosted_api import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


class TestAgentSignup:
    def test_signup_returns_key(self, client):
        r = client.post("/v1/agent/signup", json={"identity": f"anon-{uuid.uuid4().hex[:12]}"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["key"].startswith("tt_")
        assert data["team_id"]
        assert data["tier"] == "free"

    def test_signup_device_header(self, client):
        ident = f"anon-{uuid.uuid4().hex[:12]}"
        r = client.post("/v1/agent/signup", headers={"X-Device-Id": ident}, json={})
        assert r.status_code == 200
        assert r.json()["identity"] == ident

    def test_signup_generated_identity_when_none(self, client):
        r = client.post("/v1/agent/signup", json={})
        assert r.status_code == 200
        assert r.json()["identity"].startswith("anon-")

    def test_rate_limit_per_identity(self, client):
        ident = f"anon-{uuid.uuid4().hex[:12]}"
        for _ in range(3):
            client.post("/v1/agent/signup", json={"identity": ident})
        r = client.post("/v1/agent/signup", json={"identity": ident})
        assert r.status_code == 429
