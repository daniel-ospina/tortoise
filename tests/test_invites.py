"""D7 tests — invite + RBAC primitives (E3/E4/E8 use these SDK paths).

Epic: 2026-08-07-tortoise-user-journeys · Issue: #574 (D7)
Plan §6.2 E3/E4/E8: token-only accept (decision 1e), admin/member roles,
owner not invitable/removable.
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import pytest

from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    with tempfile.TemporaryDirectory() as tmpdir:
        sdk = TortoiseSDK(os.path.join(tmpdir, "test.db"), namespace="test-invites")
        yield sdk


class TestInvitePrimitives:
    def test_invitation_create_returns_token_once(self, sdk):
        team = sdk.team_create("invite-team")
        inv = sdk.invitation_create(team["id"], "bob@example.com", "admin", "owner-user")
        assert inv.get("token")
        assert inv.get("id")

    def test_invitation_duplicate_rejected(self, sdk):
        team = sdk.team_create("dup-team")
        sdk.invitation_create(team["id"], "bob@example.com", "admin", "u1")
        from tortoise.exceptions import ControlPlaneError
        with pytest.raises(ControlPlaneError):
            sdk.invitation_create(team["id"], "bob@example.com", "admin", "u1")

    def test_membership_create_validates_role(self, sdk):
        team = sdk.team_create("role-team")
        from tortoise.exceptions import ControlPlaneError
        with pytest.raises(ControlPlaneError):
            sdk.membership_create(team["id"], "u2", "not-a-role")

    def test_membership_list(self, sdk):
        team = sdk.team_create("list-team")
        sdk.membership_create(team["id"], "u2", "admin")
        members = sdk.membership_list(team["id"])
        assert len(members) >= 1
