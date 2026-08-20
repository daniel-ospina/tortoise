"""E2E-6-D — owner-only data export + team deletion (security baseline #302).

Reconstructed case (#303; E2E-6-D marker survives in tests/test_export_delete.py
+ hosted_api.py:3379 — those run in-process with dependency overrides; THIS
leg exercises the real socket + real session-JWT auth). Register-created teams
have NO Membership node (register provisions Team+APIKey only), so the owner
tenant here is provisioned via /internal/provision with created_by = the JWT
sub — which also covers the internal provision surface + FASTAPI_INTERNAL_KEY
contract.

Negatives: export without auth → 401; export with a tt_ key (non-session) →
401; export by a non-member JWT → 403; export of a deleted team → 410.
"""
from __future__ import annotations  # noqa: I001

import os
import uuid

import pytest

from conftest import INTERNAL_KEY, skip_unless_hosted_e2e

skip_unless_hosted_e2e()

pytestmark = pytest.mark.skipif(
    bool(os.environ.get("E2E_BASE_URL", "").strip()),
    reason="E2E-6-D provisions via /internal/provision (local FASTAPI_INTERNAL_KEY seam)",
)


def _provision_owner_tenant(api, user_id: str) -> dict:
    """Team + APIKey + owner Membership in one internal call."""
    team_id = f"e2e6d-{uuid.uuid4().hex[:10]}"
    r = api.post("/internal/provision",
                 headers={"Authorization": f"Bearer {INTERNAL_KEY}"},
                 data={"team_id": team_id, "team_name": f"E2E6D {team_id[-6:]}",
                       "api_key_hash": "e2e:unused-placeholder-hash",
                       "created_by": user_id})
    assert r.status == 200, f"internal provision: {r.status} {r.text()}"
    return r.json()


def test_owner_export_and_delete_lifecycle(api, session_jwt):
    """Positive: owner JWT exports the graph (schema payload incl. points),
    deletes the team (202), and the export surface closes (410)."""
    user_id, tok = session_jwt()
    h = {"Authorization": f"Bearer {tok}"}
    prov = _provision_owner_tenant(api, user_id)
    team_id = prov["team_id"]

    # No API key was revealed by provision — mint one via the session path so
    # the tenant has data worth exporting (also exercises /v1/session/key).
    r = api.post("/v1/session/key", headers=h,
                 data={"team_id": team_id, "purpose": "recovery"})
    assert r.status == 200, f"session key mint: {r.status} {r.text()}"
    tt_key = r.json()["key"]  # /v1/session/key returns {"key": ...}
    assert tt_key.startswith("tt_")

    hk = {"Authorization": f"Bearer {tt_key}"}
    r = api.post("/v1/points", headers=hk,
                 data={"content": "exportable knowledge (E2E-6-D)", "kind": "statement"})
    assert r.status == 200, r.text()

    r = api.get(f"/v1/teams/{team_id}/export", headers=h)
    assert r.status == 200, f"owner export: {r.status} {r.text()}"
    export = r.json()
    assert export.get("schema_version"), export.keys()
    assert any("exportable knowledge" in (p.get("content") or "")
               for p in export.get("points", [])), "export must include the point"
    assert export.get("team", {}).get("id") == team_id

    # Owner deletion → 202 (soft delete + grace window)
    r = api.delete(f"/v1/teams/{team_id}", headers=h)
    assert r.status == 202, f"team delete: {r.status} {r.text()}"

    r = api.get(f"/v1/teams/{team_id}/export", headers=h)
    assert r.status == 410, f"export of deleted team must 410, got {r.status}"


def test_export_requires_session_auth_401(api, tenant_factory):
    """Unauthenticated and tt_-key callers never reach the export."""
    t = tenant_factory("export-auth")
    r = api.get(f"/v1/teams/{t['team_id']}/export")
    assert r.status == 401, f"no auth must 401, got {r.status}"
    r = api.get(f"/v1/teams/{t['team_id']}/export",
                headers={"Authorization": f"Bearer {t['api_key']}"})
    assert r.status == 401, f"tt_ key on session-only route must 401, got {r.status}"


def test_export_foreign_owner_403(api, session_jwt, tenant_factory):
    """A valid session user who is NOT a member of the team gets 403."""
    other_user, other_tok = session_jwt()  # fresh user, no memberships  # noqa: RUF059
    t = tenant_factory("export-foreign")
    r = api.get(f"/v1/teams/{t['team_id']}/export",
                headers={"Authorization": f"Bearer {other_tok}"})
    assert r.status == 403, f"foreign owner must 403, got {r.status}: {r.text()}"
