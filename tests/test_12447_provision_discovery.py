"""#12447 — a builder must be able to DISCOVER that they may provision a graph
per end-user, from the API alone.

The capability is real and was never in doubt: `POST
/v1/organizations/{org_id}/graphs` (`hosted_api.create_org_graph`) provisions a
custom graph, mints that graph's `tk_` key in the same call, and is
**one-level-deep by construction** — a minted (`deleg=0`) key can never carry
`graphs:create` (`hosted_api._MINTABLE_SCOPES`' child policy + the DB CHECK
`chk_minted_key_no_escalation` + `hosted_api.create_org_graph`'s deleg gate).

What was broken is that nothing SAID so. Measured 2026-11-13 on a real
credential: `POST /v1/graphs` (the session alias a builder tries first) answered
`401 {"detail": "Invalid session token"}` — no door, no scope, no remedy — so
the one working door was the fourth thing tried.

These tests pin the two halves of the fix and, just as importantly, pin that the
fix did not buy discoverability by weakening the security model:

* every refusal on the provisioning path names the org-level endpoint and the
  `graphs:create` scope, in the `{error_code, message}` shape that
  `GRAPH_SCOPED_TEAM_SURFACE` already sanctioned (`_reject_graph_bound_org_surface`);
* an API key on the session door is refused 403 (a credential-scope refusal),
  never 401 (which reads as "your key is bad");
* a credential-less call and a session call are untouched — the guard claims
  exactly one credential class;
* one-level-deep still holds: a mint never confers `graphs:create`, and a key
  that somehow carries it while `deleg=0` still cannot provision.
"""

from __future__ import annotations

import uuid

import pytest

from tests.test_hosted_api import (  # the provisioning suite's own harness
    TestProvisioningService as _ProvisioningHarness,
)
from tests.test_hosted_api import (
    _mint_caller_key,
)

#: The ACL layer is a per-graph FalkorDB USER (defense-in-depth), orthogonal to
#: every refusal this module pins. `TORTOISE_ACL_LAYER=off` is the HOSTED
#: posture (`fly.toml` ships it) and it is what a passwordless private lane can
#: honestly exercise: `tools/test_lane.py` starts its container WITHOUT
#: `requirepass` by design (see its docstring), and a strict layer refuses to
#: mint a per-graph user over an open default user — so a 201 is UNREACHABLE
#: there in any posture but this one. Declaring the posture is not a skip: the
#: 201 path (the mint, the rollback, the envelope) is exactly what is exercised.
_HOSTED_ACL_POSTURE = {"TORTOISE_ACL_LAYER": "off"}

#: The two facts a builder cannot infer, and the reason the refusal carries
#: prose at all. Asserted as literals (not imported from the module) so a
#: silent rename in `hosted_api` reds HERE rather than agreeing with itself.
ORG_ENDPOINT = "POST /v1/organizations/{org_id}/graphs"
PROVISION_SCOPE = "graphs:create"


def _setup(tmp_path, tier: str = "pro", max_graphs=None):
    """The provisioning suite's own registry harness, under the hosted ACL
    posture (see `_HOSTED_ACL_POSTURE`)."""
    return _ProvisioningHarness()._setup(tmp_path, tier, max_graphs)


@pytest.fixture(autouse=True)
def _hosted_acl_posture(monkeypatch):
    """Declare the hosted ACL posture for every test in this module.

    `tests/conftest.py` CLEARS ``TORTOISE_ACL_LAYER`` per test (so an ambient
    export cannot change a suite); this module re-declares it deliberately, for
    the reason in `_HOSTED_ACL_POSTURE` — a passwordless private lane cannot
    host a strict layer's per-graph user, and nothing here is about the ACL.
    """
    for name, value in _HOSTED_ACL_POSTURE.items():
        monkeypatch.setenv(name, value)


def _detail(resp) -> dict:
    """The refusal body, asserting the sanctioned shape on the way through.

    `GRAPH_SCOPED_TEAM_SURFACE` (#2114) is `{"error_code", "message"}` — a
    credential-scope refusal gets ONE style, so a second style here would be
    the defect this change exists to avoid."""
    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, dict), detail
    assert set(detail) == {"error_code", "message"}, detail
    return detail


def _honest_graph_scoped_key(sdk, org_id: str, graph_id: str = "g_enduser") -> str:
    """A `tk_` per-graph key with a data scope and `deleg=0` — i.e. exactly what
    provisioning mints for an end user, written straight to the registry so the
    test does not depend on the mint path it is testing."""
    token = "tk_" + uuid.uuid4().hex
    from tortoise.auth import hash_api_key
    sdk._get_registry().query(
        "CREATE (k:APIKey {id:$id, org_id:$tid, key_hash:$kh, key_prefix:$kp, "
        "created_by:'x', graph_id:$gid, scopes:['graphs:read'], "
        "delegation_depth:0})",
        params={"id": f"minted-{uuid.uuid4().hex[:8]}", "tid": org_id,
                "kh": hash_api_key(token), "kp": token[:10], "gid": graph_id},
    )
    return token


class TestTheWrongDoorTeachesTheRightOne:
    """`POST /v1/graphs` is session-only. An API key there is at the wrong door,
    and the wrong-door refusal must name the right one (#12447 acceptance 1)."""

    def test_graph_scoped_key_on_the_session_door_names_endpoint_and_scope(
            self, tmp_path):
        """The measured failure: a builder's per-graph key on `/v1/graphs`
        returned `401 Invalid session token`. It must now say which door and
        which scope — and must stay a 403, because the key is VALID."""
        gen = _setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            token = _honest_graph_scoped_key(sdk, tid)
            r = tc.post("/v1/graphs",
                        json={"org_id": tid, "name": "enduser-1"},
                        headers={"Authorization": f"Bearer {token}"})
            detail = _detail(r)
            assert detail["error_code"] == "PROVISION_VIA_ORG_ENDPOINT", detail
            assert ORG_ENDPOINT in detail["message"], detail
            assert PROVISION_SCOPE in detail["message"], detail
            # The refusal states the property, because a builder who reads
            # "denied" retries the same call.
            assert "one-level-deep" in detail["message"], detail
            # …and it points at the half of the flow nothing documented: the
            # same call that provisions also mints the graph's key.
            assert "mints" in detail["message"], detail

        finally:
            gen.close()

    def test_account_level_key_on_the_session_door_gets_the_same_map(
            self, tmp_path):
        """An account-level key that COULD provision still 401'd here — that is
        the discoverability defect at its worst: the caller held the
        capability and was told only that their token was invalid."""
        gen = _setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            key = _mint_caller_key(sdk, tid, scopes=["graphs:create"])
            r = tc.post("/v1/graphs",
                        json={"org_id": tid, "name": "enduser-1"},
                        headers={"Authorization": f"Bearer {key['api_key']}"})
            detail = _detail(r)
            assert detail["error_code"] == "PROVISION_VIA_ORG_ENDPOINT", detail
            assert ORG_ENDPOINT in detail["message"], detail
            assert PROVISION_SCOPE in detail["message"], detail
        finally:
            gen.close()


class TestTheGuardClaimsOneCredentialClassOnly:
    """The refusal is narrow on purpose: it must not change what a session call
    or a credential-less call sees."""

    def test_no_credential_is_still_401_not_403(self, tmp_path):
        """No Authorization header is an AUTH failure, not a scope refusal —
        `401 Missing session token`, unchanged."""
        gen = _setup(tmp_path)
        _sdk, tid, tc = next(gen)
        try:
            r = tc.post("/v1/graphs", json={"org_id": tid, "name": "x"})
            assert r.status_code == 401, r.text
            # The contract is the STATUS **and** the detail: this arm must take
            # the unchanged `get_current_user` path verbatim. A status-only
            # assertion stays green if the new refusal shape ever leaks here
            # (measured: 401 {"detail": "Missing session token"}).
            assert r.json()["detail"] == "Missing session token", r.text
        finally:
            gen.close()

    def test_a_session_call_still_provisions_and_a_minted_key_is_never_granted(
            self, tmp_path):
        """The session lane is untouched (201 + the minted key), and the key it
        mints cannot provision: the discoverability fix did not buy anything by
        widening `graphs:create`."""
        import tortoise.hosted_api as ha_mod
        gen = _setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            ha_mod.app.dependency_overrides[ha_mod.get_current_user] = \
                lambda: {"user_id": "session-owner", "email": "o@x.com"}
            sdk._get_registry().query(
                "MERGE (m:Membership {user_id:'session-owner', org_id:$tid, "
                "status:'active'}) SET m.role='owner'",
                params={"tid": tid},
            )
            try:
                r = tc.post("/v1/graphs",
                            json={"org_id": tid, "name": "from-dash",
                                  "scopes": ["graphs:read", "graphs:create"]})
                assert r.status_code == 201, r.text
                body = r.json()
                assert body["key_plaintext"].startswith("tk_")
                assert body["revealed_once"] is True
                # The escalation scope was REQUESTED and stripped (child policy).
                assert body["key"]["scopes"] == ["graphs:read"], body["key"]
            finally:
                ha_mod.app.dependency_overrides.pop(
                    ha_mod.get_current_user, None)

            # ⛔ ONE-LEVEL-DEEP, on the door that is now DISCOVERABLE: the key
            # just minted (which asked for graphs:create) cannot provision.
            token = body["key_plaintext"]
            r2 = tc.post(f"/v1/organizations/{tid}/graphs",
                         json={"name": "nested-attempt"},
                         headers={"Authorization": f"Bearer {token}"})
            detail = _detail(r2)
            # EXACTLY this code, not a set: every provisioned key carries a
            # data scope (`_mint_graph_key` defaults to `graphs:read`), so
            # `get_current_org_gated`'s dormancy arm passes it through and the
            # provisioning door's own deleg gate is what refuses it. Accepting
            # `KEY_NOT_USER_MINTED` here would stay green if that ever changed,
            # while the refusal this PR ships became unreachable.
            assert detail["error_code"] == "PROVISION_REQUIRES_ORG_KEY", detail
            # …and the refusal points at the account-level door, never at a
            # way to elevate this key.
            assert ORG_ENDPOINT in detail["message"], detail
        finally:
            gen.close()


class TestTheListingDoorTeachesTheSameWay:
    """#12447 measured `GET /v1/graphs` → `401` as the second wall. Teaching
    only the create door would leave a builder who has ALREADY provisioned
    unable to enumerate their graphs, answered by the same "your key is bad"."""

    def test_api_key_on_the_listing_door_names_the_real_remedy(self, tmp_path):
        gen = _setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            token = _honest_graph_scoped_key(sdk, tid)
            r = tc.get(f"/v1/graphs?org_id={tid}",
                       headers={"Authorization": f"Bearer {token}"})
            detail = _detail(r)
            assert detail["error_code"] == "GRAPH_LISTING_VIA_SESSION", detail
            # The remedy must name what EXISTS. A first draft said no key-driven
            # route returns a graph id, and a second named a control-plane SDK
            # call a hosted builder cannot use — `GET /v1/team/keys` and the MCP
            # tool `tortoise_list_graphs` are the routes that actually answer, so
            # this pins the truthful remedy rather than the convenient one.
            assert ORG_ENDPOINT in detail["message"], detail
            assert "/v1/team/keys" in detail["message"], detail
            assert "tortoise_list_graphs" in detail["message"], detail
            # `list_memory_graphs` is a CONTROL-PLANE SDK call (no hosted
            # credential, no HTTP transport) — citing it here would send a
            # hosted builder to a call that answers `[]`.
            assert "list_memory_graphs" not in detail["message"], detail
            assert "no key-driven listing route" not in detail["message"], detail
        finally:
            gen.close()

    def test_an_account_key_recovers_graph_ids_through_the_keys_surface(
            self, tmp_path):
        """The refusal tells the builder graph ids ARE reachable with a key.
        That claim is only worth making if it EXECUTES — so run it: provision
        two graphs, then recover both ids from `GET /v1/team/keys` with the
        account's full-access key (`_require_keys_manage` passes
        `legacy_full_access`)."""
        gen = _setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            account_key = _mint_caller_key(sdk, tid, scopes=None)  # legacy shape
            provisioned = []
            for name in ("eu-list-1", "eu-list-2"):
                r = tc.post(f"/v1/organizations/{tid}/graphs",
                            json={"name": name},
                            headers={"Authorization":
                                     f"Bearer {account_key['api_key']}"})
                assert r.status_code == 201, r.text
                provisioned.append(r.json()["graph"]["id"])
            r = tc.get("/v1/team/keys",
                       headers={"Authorization":
                                f"Bearer {account_key['api_key']}"})
            assert r.status_code == 200, r.text
            # The shape is `{"keys": [...]}` (not a bare list) — read it as the
            # API returns it, so the test pins the real contract.
            rows = r.json()["keys"]
            recovered = {row.get("graph_id") for row in rows}
            assert set(provisioned) <= recovered, (provisioned, recovered)
        finally:
            gen.close()

    def test_a_session_listing_is_untouched(self, tmp_path):
        import tortoise.hosted_api as ha_mod
        gen = _setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            ha_mod.app.dependency_overrides[ha_mod.get_current_user] = \
                lambda: {"user_id": "list-owner", "email": "o@x.com"}
            sdk._get_registry().query(
                "MERGE (m:Membership {user_id:'list-owner', org_id:$tid, "
                "status:'active'}) SET m.role='owner'",
                params={"tid": tid},
            )
            try:
                r = tc.get(f"/v1/graphs?org_id={tid}")
                assert r.status_code == 200, r.text
                assert r.json()[0]["kind"] == "default"
            finally:
                ha_mod.app.dependency_overrides.pop(
                    ha_mod.get_current_user, None)
        finally:
            gen.close()


class TestTheKeyDrivenDoorNamesWhatIsMissing:
    """`POST /v1/organizations/{org_id}/graphs` had the two cryptic refusals —
    `"Minted keys cannot provision graphs"` and `"Missing graphs:create
    scope"` — neither of which named the scope as a remedy or the rule as a
    property."""

    def test_minted_key_refusal_states_one_level_deep_as_the_property(
            self, tmp_path):
        gen = _setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            # A deleg=0 key carrying graphs:create, written past the app mint
            # (the DB CHECK is production's backstop; the endpoint must 403
            # regardless — E2E-4-negative).
            from tortoise.auth import hash_api_key
            token = "tk_" + uuid.uuid4().hex
            sdk._get_registry().query(
                "CREATE (k:APIKey {id:$id, org_id:$tid, key_hash:$kh, "
                "key_prefix:$kp, created_by:'x', graph_id:'g_x', "
                "scopes:['graphs:read', 'graphs:create'], "
                "delegation_depth:0})",
                params={"id": "minted-with-create", "tid": tid,
                        "kh": hash_api_key(token), "kp": token[:10]},
            )
            r = tc.post(f"/v1/organizations/{tid}/graphs",
                        json={"name": "nested"},
                        headers={"Authorization": f"Bearer {token}"})
            detail = _detail(r)
            assert detail["error_code"] == "PROVISION_REQUIRES_ORG_KEY", detail
            assert PROVISION_SCOPE in detail["message"], detail
            assert ORG_ENDPOINT in detail["message"], detail
            assert "one-level-deep" in detail["message"], detail
        finally:
            gen.close()

    def test_scope_gap_refusal_names_the_missing_scope(self, tmp_path):
        gen = _setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            key = _mint_caller_key(sdk, tid, scopes=["graphs:read"])
            r = tc.post(f"/v1/organizations/{tid}/graphs",
                        json={"name": "no-scope"},
                        headers={"Authorization": f"Bearer {key['api_key']}"})
            detail = _detail(r)
            assert detail["error_code"] == "PROVISION_SCOPE_REQUIRED", detail
            assert PROVISION_SCOPE in detail["message"], detail
            assert ORG_ENDPOINT in detail["message"], detail
        finally:
            gen.close()


class TestOneLevelDeepIsStillTheSecurityModel:
    """The constraint IS the feature. Discoverability must never be bought by
    granting `graphs:create` to a minted key (#12447 constraint, #4010: no
    capture caps are touched here either)."""

    def test_the_mint_strips_the_escalation_scope_when_asked_for_it(
            self, tmp_path):
        gen = _setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            key = _mint_caller_key(sdk, tid, scopes=["graphs:create"])
            r = tc.post(f"/v1/organizations/{tid}/graphs",
                        json={"name": "enduser-a",
                              "scopes": ["graphs:read", "graphs:create",
                                         "graphs:delete", "team:manage"]},
                        headers={"Authorization": f"Bearer {key['api_key']}"})
            assert r.status_code == 201, r.text
            body = r.json()
            # Child policy: only data scopes survive the mint.
            assert body["key"]["scopes"] == ["graphs:read"], body["key"]
            assert set(body["key"]["scopes"]) <= {"graphs:read", "graphs:write"}
            # The stored node agrees (deleg=0, no escalation scope) — the
            # envelope is not the only witness.
            row = sdk._get_registry().query(
                "MATCH (k:APIKey {id:$kid}) RETURN k.delegation_depth, "
                "k.scopes",
                params={"kid": body["key"]["id"]}).result_set[0]
            assert row[0] == 0
            assert PROVISION_SCOPE not in (row[1] or []), row

        finally:
            gen.close()

    def test_the_mintable_scope_policy_excludes_the_provisioning_scope(self):
        """The policy as a SET, so a future edit that adds `graphs:create` to
        `_MINTABLE_SCOPES` reds here even if every behaviour test happens to
        pass on a lenient path."""
        import tortoise.hosted_api as ha_mod
        assert PROVISION_SCOPE not in ha_mod._MINTABLE_SCOPES
        assert set(ha_mod._MINTABLE_SCOPES) <= {"graphs:read", "graphs:write"}

    def test_provision_then_use_is_one_call(self, tmp_path):
        """The end-to-end promise the docs now state: ONE call provisions the
        end user's graph, mints its key, and that key WORKS on that graph —
        while the account-level key stays the only thing that can provision."""
        gen = _setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            key = _mint_caller_key(sdk, tid, scopes=["graphs:create"])
            r = tc.post(f"/v1/organizations/{tid}/graphs",
                        json={"name": "enduser-b",
                              "scopes": ["graphs:read", "graphs:write"]},
                        headers={"Authorization": f"Bearer {key['api_key']}"})
            assert r.status_code == 201, r.text
            body = r.json()
            assert body["graph"]["namespace"].startswith(f"org_{tid}_")
            # The minted credential authenticates on the DATA plane (that is
            # what "hand them the key" means), so the flow the docs describe is
            # the flow that runs.
            minted = body["key_plaintext"]
            r2 = tc.get("/v1/points",
                        headers={"Authorization": f"Bearer {minted}"})
            assert r2.status_code == 200, r2.text
            # …and cannot provision. The account key remains the provisioner.
            r3 = tc.post(f"/v1/organizations/{tid}/graphs",
                         json={"name": "enduser-c"},
                         headers={"Authorization": f"Bearer {minted}"})
            assert r3.status_code == 403, r3.text
        finally:
            gen.close()




if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
