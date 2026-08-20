"""E2E-4-D — tenant isolation (registry portion, over real sockets).

Reconstructed case (#303; 7714-data-model.md:45). Two independently
provisioned tenants: tenant B can neither read nor list tenant A's points;
revoked keys stop authenticating immediately.

Negatives: foreign point id → 404; revoked key → 401; garbage bearer → 401.
"""
from __future__ import annotations  # noqa: I001

import pytest

from conftest import is_remote_mode, skip_unless_hosted_e2e

skip_unless_hosted_e2e()


@pytest.mark.skipif(is_remote_mode(), reason="needs two distinct tenants (remote pool shares 3)")
def test_cross_tenant_point_read_denied(api, tenant_factory):
    """Positive for A, negative across the boundary: B's key cannot read A's
    point by id and A's content never appears in B's list."""
    a = tenant_factory("iso-a")
    b = tenant_factory("iso-b")
    ha = {"Authorization": f"Bearer {a['api_key']}"}
    hb = {"Authorization": f"Bearer {b['api_key']}"}

    r = api.post("/v1/points", headers=ha,
                 data={"content": "tenant A secret (E2E-4-D)", "kind": "statement"})
    assert r.status == 200, r.text()
    a_point = r.json()["id"]

    # B reading A's point id → not found (graphs are separate namespaces)
    r = api.get(f"/v1/points/{a_point}", headers=hb)
    assert r.status == 404, f"foreign point must 404, got {r.status}: {r.text()}"

    # B's list must not contain A's content
    r = api.get("/v1/points", headers=hb)
    assert r.status == 200
    assert all("tenant A secret" not in p.get("content", "")
               for p in r.json()["points"])

    # A still reads its own point fine
    r = api.get(f"/v1/points/{a_point}", headers=ha)
    assert r.status == 200 and r.json()["content"] == "tenant A secret (E2E-4-D)"


def test_revoked_key_stops_authenticating(api, tenant_factory):
    """Revoke a minted key → immediate 401 (no grace)."""
    t = tenant_factory("revoke")
    h = {"Authorization": f"Bearer {t['api_key']}"}
    r = api.post("/v1/team/keys", headers=h)
    assert r.status == 200, r.text()
    new_key = r.json()["key"]
    key_id = r.json()["id"]

    hk = {"Authorization": f"Bearer {new_key}"}
    assert api.get("/v1/team", headers=hk).status == 200

    r = api.delete(f"/v1/team/keys/{key_id}", headers=h)
    assert r.status == 200, r.text()
    r = api.get("/v1/team", headers=hk)
    assert r.status == 401, f"revoked key must 401 immediately, got {r.status}"


def test_garbage_bearer_401(api):
    r = api.get("/v1/team", headers={"Authorization": "Bearer tt_garbage_not_real"})
    assert r.status == 401
