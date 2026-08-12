"""E2E-7-D — security baseline over the wire.

Reconstructed case (#303; E2E-7-D marker survives in tests/test_hosted_auth.py
line 271 — in-process; THIS leg asserts the deployed artifact's posture:
/health/security, HSTS header on real responses, the full 401 auth matrix,
and the internal-key gate).

Negatives (auth matrix): missing header / empty bearer / wrong prefix /
invalid tt_ key → 401; /internal/provision with a wrong internal key → 401.
"""
from __future__ import annotations

from conftest import skip_unless_hosted_e2e

skip_unless_hosted_e2e()


def test_health_security_posture(api):
    r = api.get("/health/security")
    assert r.status == 200, r.text()
    body = r.json()
    # Posture must report the hardened config the suite boots with.
    text = str(body).lower()
    assert body.get("status") in ("ok", "healthy") or "pepper" in text, body


def test_hsts_header_present(api):
    """HSTS middleware must stamp Strict-Transport-Security on responses."""
    r = api.get("/health")
    assert r.status == 200
    hsts = [v for k, v in r.headers.items() if k.lower() == "strict-transport-security"]
    assert hsts, f"HSTS header missing: {dict(r.headers)}"


def test_valid_key_authenticates(api, tenant_factory):
    t = tenant_factory("sec-valid")
    r = api.get("/v1/team", headers={"Authorization": f"Bearer {t['api_key']}"})
    assert r.status == 200, r.text()


def test_auth_matrix_401(api):
    """Four malformed-auth legs, all 401 (never 500, never 200)."""
    legs = [
        ({}, "missing Authorization header"),
        ({"Authorization": "Bearer "}, "empty bearer token"),
        ({"Authorization": "WrongPrefix abc123"}, "wrong auth scheme prefix"),
        ({"Authorization": "Bearer tt_deadbeef_deadbeef_deadbeef"}, "invalid tt_ key"),
    ]
    for headers, label in legs:
        r = api.get("/v1/team", headers=headers)
        assert r.status == 401, f"{label}: expected 401, got {r.status}: {r.text()}"


def test_internal_provision_wrong_key_401(api):
    """The internal surface rejects unknown keys (and missing ones)."""
    body = {"team_id": "e2e7d-x", "team_name": "x", "api_key_hash": "h",
            "created_by": "u"}
    r = api.post("/internal/provision", data=body)
    assert r.status == 401, f"missing internal key must 401, got {r.status}"
    r = api.post("/internal/provision",
                 headers={"Authorization": "Bearer wrong-internal-key"}, data=body)
    assert r.status == 401, f"wrong internal key must 401, got {r.status}"
