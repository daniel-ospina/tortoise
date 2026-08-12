"""E2E-8-D — multi-team membership, invites, RBAC (session-JWT plane).

Reconstructed case (#303; E2E-8-D marker survives in 7714-data-model.md:47).
A session user owns two JWT-created teams (owner memberships); one is bumped
to tier=team via the hermetic webhook so invites unlock; an invited member
accepts with their own JWT; RBAC holds (member cannot manage members;
cross-team key revocation 403s).

Negatives: non-owner member management → 403; foreign-team key revoke → 403;
duplicate invite → 409; garbage invite token → 400.
"""
from __future__ import annotations

import uuid

from conftest import bump_team_tier, skip_unless_hosted_e2e

skip_unless_hosted_e2e()


def _create_team(api, jwt_headers, name_suffix):
    name = f"e2e8d-{name_suffix}-{uuid.uuid4().hex[:6]}"
    r = api.post("/v1/teams", headers=jwt_headers, data={"name": name})
    assert r.status == 200, f"team create: {r.status} {r.text()}"
    return r.json()


def test_multi_team_ownership_and_listing(api, session_jwt):
    """One session user creates two teams; GET /v1/teams lists both."""
    user_id, tok = session_jwt()
    h = {"Authorization": f"Bearer {tok}"}
    t1 = _create_team(api, h, "alpha")
    t2 = _create_team(api, h, "beta")

    r = api.get("/v1/teams", headers=h)
    assert r.status == 200, r.text()
    ids = {t.get("id") or t.get("team_id") for t in r.json()}
    for created in (t1, t2):
        cid = created.get("id") or created.get("team_id")
        assert cid in ids, f"created team {cid} missing from /v1/teams: {ids}"


def test_invite_accept_flow_with_rbac(api, session_jwt):
    """Team-tier team → invite member → accept (second JWT) → listed; the
    member cannot manage membership (403)."""
    owner_id, owner_tok = session_jwt()
    ho = {"Authorization": f"Bearer {owner_tok}"}
    team = _create_team(api, ho, "invitable")
    team_id = team.get("id") or team.get("team_id")

    bump_team_tier(api, team_id, "team")

    invitee_email = f"e2e-invitee-{uuid.uuid4().hex[:8]}@e2e.premise-labs.dev"
    r = api.post("/v1/invites", headers=ho,
                 data={"team_id": team_id, "email": invitee_email, "role": "member"})
    assert r.status == 200, f"invite mint: {r.status} {r.text()}"
    token = r.json()["token"]

    # Acceptance validates the JWT email claim against the invite email.
    member_id, member_tok = session_jwt(email=invitee_email)
    hm = {"Authorization": f"Bearer {member_tok}"}
    r = api.post("/v1/invites/accept", headers=hm, data={"token": token})
    assert r.status == 200, f"invite accept: {r.status} {r.text()}"

    r = api.get(f"/v1/teams/{team_id}/members", headers=ho)
    assert r.status == 200, r.text()
    member_ids = {m.get("user_id") for m in r.json()}
    assert member_id in member_ids, f"accepted member missing: {member_ids}"

    # RBAC: a plain member cannot remove members (owner/admin only)
    r = api.delete(f"/v1/teams/{team_id}/members/{owner_id}", headers=hm)
    assert r.status == 403, f"member removing owner must 403, got {r.status}"


def test_duplicate_invite_409_and_bad_token_400(api, session_jwt):
    owner_id, owner_tok = session_jwt()
    ho = {"Authorization": f"Bearer {owner_tok}"}
    team = _create_team(api, ho, "dupinvite")
    team_id = team.get("id") or team.get("team_id")
    bump_team_tier(api, team_id, "team")

    email = f"e2e-dup-{uuid.uuid4().hex[:8]}@e2e.premise-labs.dev"
    r1 = api.post("/v1/invites", headers=ho,
                  data={"team_id": team_id, "email": email, "role": "member"})
    assert r1.status == 200, r1.text()
    r2 = api.post("/v1/invites", headers=ho,
                  data={"team_id": team_id, "email": email, "role": "member"})
    assert r2.status == 409, f"duplicate invite must 409, got {r2.status}: {r2.text()}"

    _, member_tok = session_jwt()
    r = api.post("/v1/invites/accept", headers={"Authorization": f"Bearer {member_tok}"},
                 data={"token": "tt_this_is_not_an_invite_token"})
    assert r.status == 400, f"bad invite token must 400, got {r.status}: {r.text()}"


def test_cross_team_key_revoke_403(api, tenant_factory):
    """Team A's key cannot revoke team B's key ('Not your API key')."""
    a = tenant_factory("rbac-a")
    b = tenant_factory("rbac-b")
    ha = {"Authorization": f"Bearer {a['api_key']}"}
    hb = {"Authorization": f"Bearer {b['api_key']}"}

    r = api.post("/v1/team/keys", headers=hb)
    assert r.status == 200, r.text()
    b_key_id = r.json()["id"]

    r = api.delete(f"/v1/team/keys/{b_key_id}", headers=ha)
    assert r.status == 403, f"cross-team revoke must 403, got {r.status}: {r.text()}"
