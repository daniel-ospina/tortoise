"""E2E-1-D — signup → tenant provisioning → API key → first Point.

Reconstructed case (#303; design docs lost in migration — see plan
docs/plans/2026-08-12-303-hosted-e2e-suite.md). Journey start: the public
/v1/register surface provisions team + tt_ key + tenant graph; the key
authenticates /v1/team; the first Point round-trips through the data plane.

Negatives: duplicate email (409), short password (422), malformed email (422).
"""
from __future__ import annotations

import uuid

import pytest

from conftest import is_remote_mode, skip_unless_hosted_e2e

skip_unless_hosted_e2e()

# #303 (review r2): direct /v1/register calls consume a token from the
# server-side 3/hr/IP register budget BEFORE validation (even 422s count),
# which starves the shared remote tenant pool. The register surface is fully
# covered in local hermetic mode (RATE_LIMIT_DISABLED=1); remote mode runs
# the journey via the pooled tenant_factory instead.
pytestmark = pytest.mark.skipif(
    is_remote_mode(),
    reason=("direct /v1/register calls burn the 3/hr/IP register budget the "
            "shared tenant pool needs; register surface covered in local mode"))


def test_register_provisions_team_key_and_first_point(api, tenant_factory):
    """Positive chain: register → key authenticates → Point created + listed."""
    email = f"e2e-1d-{uuid.uuid4().hex[:8]}@e2e.premise-labs.dev"
    r = api.post("/v1/register", data={"email": email, "password": "E2ePass-303-x"})
    assert r.status == 200, r.text()
    body = r.json()
    assert body["api_key"].startswith("tt_"), "API key must be a tt_ key"
    assert body["team_id"] and body["graph_name"], r.text()

    h = {"Authorization": f"Bearer {body['api_key']}"}
    r = api.get("/v1/team", headers=h)
    assert r.status == 200, r.text()
    team = r.json()
    assert team["tier"] == "free"

    r = api.post("/v1/points", headers=h,
                 data={"content": "first memory (E2E-1-D)", "kind": "statement"})
    assert r.status == 200, r.text()
    point_id = r.json()["id"]
    assert point_id

    r = api.get("/v1/points", headers=h)
    assert r.status == 200, r.text()
    contents = [p["content"] for p in r.json()["points"]]
    assert "first memory (E2E-1-D)" in contents


def test_register_duplicate_email_409(api):
    email = f"e2e-1d-dup-{uuid.uuid4().hex[:8]}@e2e.premise-labs.dev"
    r = api.post("/v1/register", data={"email": email, "password": "E2ePass-303-x"})
    assert r.status == 200, r.text()
    r = api.post("/v1/register", data={"email": email, "password": "E2ePass-303-x"})
    assert r.status == 409, f"duplicate email must 409, got {r.status}: {r.text()}"
    assert "already_registered" in r.text()


def test_register_short_password_422(api):
    email = f"e2e-1d-pw-{uuid.uuid4().hex[:8]}@e2e.premise-labs.dev"
    r = api.post("/v1/register", data={"email": email, "password": "shrt7!"})
    assert r.status == 422, f"7-char password must 422 (min 8), got {r.status}"


def test_register_malformed_email_422(api):
    r = api.post("/v1/register",
                 data={"email": "not-an-email", "password": "E2ePass-303-x"})
    assert r.status == 422, f"malformed email must 422, got {r.status}"
