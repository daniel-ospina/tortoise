"""#10046: two organizations in one process never share a SESSION/OAuth tenant.

The sibling module (`test_10046_session_oauth_record_tenant.py`) pins the
WIRING: a session/JWT credential and an OAuth ``oat_`` token each name the
org's default-graph row, so their armed record writes stop being refused
``no_tenant``. It pins that for ONE organization at a time.

This module is the other half of the question #10046's acceptance never asked,
and the one that matters once a second tenant exists:

    with TWO organizations live in one process, can org A's credential come
    back carrying org B's tenant?

``record_tenant_for`` is a pure function of the credential dict, so a leaked
tenant cannot come from it — it has to come from the RESOLVER that built the
dict (``hosted_api._session_user_org``, ``oauth.resolve_oauth_access_token``):
a module-level cache, a ContextVar outliving its request, or an org that
defaults to "the first membership" instead of the resolved one. So every test
here ALTERNATES between the two tenants (A, B, A again) instead of resolving
each once: a value that leaked out of A's resolution surfaces on the SECOND or
THIRD read, which a single-shot assertion cannot see.

WHY NO MIGRATION IS PINNED HERE (the #10046 framing, measured). The record has
no credential-side tenant column to backfill: `supabase/migrations/
20261012000001_record_event_stream.sql:53-54` makes `org_id text NOT NULL` and
`graph_id text NOT NULL` on every record table, with the FK
`(org_id, graph_id) -> public.graphs (org_id, id)` (a unique index on
`public.graphs (org_id, id)` is created for it, `:50`). A tenantless record row
is therefore not representable, and org erasure is a CASCADE from
`public.organizations` (`20261012000005_record_org_purge_rpc.sql`), so no row
can be missed for want of a tenant to scope the delete by. What CAN be missing
is a credential that names no tenant at all — and that direction is
`record_tenant_for` returning ``None`` ⇒ the write is REFUSED and counted
``no_tenant``, never filed under a guessed one (`record_writer.py:709-780`).
The refusal direction is pinned in the sibling module for ONE org; what is
pinned HERE is that the same refusal, with two tenants live, is never resolved
into the OTHER one.
"""
import asyncio

import pytest

from tests.fake_control_plane import FakeControlPlane

ORG_A = "org-10046-a"
ORG_B = "org-10046-b"
ROW_A = "g_aaaaaaaaaaaaaaaa"
ROW_B = "g_bbbbbbbbbbbbbbbb"
USER_BOTH = "11111111-1111-1111-1111-111111111111"
USER_A_ONLY = "22222222-2222-2222-2222-222222222222"
TOKEN_A = "oat_" + "a" * 40
TOKEN_B = "oat_" + "b" * 40


def _org_row(org_id: str) -> dict:
    return {
        "id": org_id, "tier": "team", "graph_name": "org_" + org_id,
        "max_users": 5, "max_graphs": 3, "graph_size_cap": 1000,
        "max_points": 1000, "suspended_at": None, "flagged_at": None,
        "email": "owner@example.test", "dashboard_key_login": True,
        "backup_enabled": True,
    }


def _default_row(org_id: str, row_id: str) -> dict:
    """Each org's default row carries a DISTINCT id.

    Both tenants share one id in the sibling module's fixture, which is fine
    for a single-tenant assertion and useless here: a shared id would make
    "A named B's row" unobservable.
    """
    return {
        "id": row_id, "org_id": org_id, "kind": "default",
        "status": "active", "namespace": "org_" + org_id, "name": "default",
    }


class _Request:
    def __init__(self, org_id: str | None = None) -> None:
        self.query_params: dict = {} if org_id is None else {"org_id": org_id}
        self.state = type("S", (), {})()
        self.headers: dict = {}


def _two_tenant_cp() -> FakeControlPlane:
    """Both orgs materialised: orgs rows, default-graph rows, memberships."""
    cp = FakeControlPlane()
    cp.seed("organizations", [_org_row(ORG_A), _org_row(ORG_B)])
    cp.seed("graphs", [_default_row(ORG_A, ROW_A), _default_row(ORG_B, ROW_B)])
    cp.seed("org_memberships", [
        {"user_id": USER_BOTH, "org_id": ORG_A, "role": "owner",
         "status": "active"},
        {"user_id": USER_BOTH, "org_id": ORG_B, "role": "owner",
         "status": "active"},
        {"user_id": USER_A_ONLY, "org_id": ORG_A, "role": "owner",
         "status": "active"},
    ])
    return cp


def _wire(monkeypatch, cp) -> None:
    """Install `cp` as the control plane and stub the non-auth side effects."""
    from tortoise import hosted_api as ha
    from tortoise import supabase_control as sc

    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(sc, "get_control_plane", lambda: cp)
    monkeypatch.setattr(ha, "_org_node_sync_limits", lambda oid: {})

    async def _noop(*a, **kw):
        return None

    monkeypatch.setattr(ha, "_abuse_post_auth", _noop)


def _session_org(monkeypatch, cp, *, user: str = USER_BOTH,
                 pin: str | None = None) -> dict:
    from tortoise import hosted_api as ha

    _wire(monkeypatch, cp)
    return asyncio.run(ha._session_user_org(_Request(pin), {"user_id": user}))


def _oauth_org(monkeypatch, cp, token: str, org_id: str) -> dict:
    from tortoise import oauth

    cp.seed("oauth_access_tokens", [{
        "token_hash": oauth._sha256(token), "client_id": "client-10046",
        "user_id": USER_BOTH, "org_id": org_id, "scope": "mcp",
        "expires_at": "2099-01-01T00:00:00+00:00", "revoked_at": None,
    }])
    _wire(monkeypatch, cp)
    return oauth.resolve_oauth_access_token(cp, token)


def _assert_names_its_own(resolved: dict, *, org_id: str, row_id: str,
                          other_org: str, other_row: str, label: str) -> None:
    """The cross-tenant checker BOTH directions, in one place.

    A checker that only asserted "the tenant is not None" would pass on the
    WRONG tenant, so the absent half (never the other org's pair) is asserted
    alongside the present half (exactly this org's pair).
    """
    from tortoise import hosted_api as ha

    assert resolved["org_id"] == org_id, (
        f"{label}: the credential resolved to {resolved['org_id']!r}, not "
        f"{org_id!r} — a credential naming another tenant's organization")
    assert resolved["default_graph_id"] == row_id, (
        f"{label}: default_graph_id is {resolved['default_graph_id']!r}, not "
        f"{row_id!r} — the tenant is not the tenant's own default row")
    assert resolved["default_graph_id"] != other_row, (
        f"{label}: default_graph_id is the OTHER tenant's row ({other_row!r}) "
        "— a cross-tenant record tenant, the write this seam exists to prevent")
    tenant = ha._resolve_record_tenant(resolved)
    assert tenant == (org_id, row_id), (
        f"{label}: record_tenant_for gave {tenant!r}, not {(org_id, row_id)!r}")
    assert tenant != (other_org, other_row), (
        f"{label}: record_tenant_for gave the OTHER tenant's pair {tenant!r}")


def test_two_session_credentials_never_share_a_tenant(monkeypatch):
    """A, then B, then A again — each session names its OWN default graph.

    Mutation this discriminates: caching the resolved default row on the org
    dict's shape, on `graph_name`, or in a module global keyed by anything
    other than `org_id` → the third read (A again, after B) returns B's row and
    the checker reddens. Single-shot resolution cannot catch it, which is why
    the alternation is the test.
    """
    cp = _two_tenant_cp()

    a1 = _session_org(monkeypatch, cp, pin=ORG_A)
    b = _session_org(monkeypatch, cp, pin=ORG_B)
    a2 = _session_org(monkeypatch, cp, pin=ORG_A)

    _assert_names_its_own(a1, org_id=ORG_A, row_id=ROW_A,
                          other_org=ORG_B, other_row=ROW_B, label="A first")
    _assert_names_its_own(b, org_id=ORG_B, row_id=ROW_B,
                          other_org=ORG_A, other_row=ROW_A, label="B")
    _assert_names_its_own(a2, org_id=ORG_A, row_id=ROW_A,
                          other_org=ORG_B, other_row=ROW_B, label="A again")


def test_two_oauth_tokens_never_share_a_tenant(monkeypatch):
    """The `oat_` class, same alternation — it resolves through its own seam.

    Mutation this discriminates: resolving the OAuth tenant from the process's
    first/named org, or reusing a previously introspected token's org → B's
    token comes back with A's row (or the reverse) and the checker reddens.
    """
    cp = _two_tenant_cp()

    a1 = _oauth_org(monkeypatch, cp, TOKEN_A, ORG_A)
    b = _oauth_org(monkeypatch, cp, TOKEN_B, ORG_B)
    a2 = _oauth_org(monkeypatch, cp, TOKEN_A, ORG_A)

    _assert_names_its_own(a1, org_id=ORG_A, row_id=ROW_A,
                          other_org=ORG_B, other_row=ROW_B, label="token A")
    _assert_names_its_own(b, org_id=ORG_B, row_id=ROW_B,
                          other_org=ORG_A, other_row=ROW_A, label="token B")
    _assert_names_its_own(a2, org_id=ORG_A, row_id=ROW_A,
                          other_org=ORG_B, other_row=ROW_B, label="token A again")


def test_a_session_cannot_be_pinned_to_a_tenant_it_does_not_belong_to(monkeypatch):
    """`?org_id=` is the ONE client-supplied tenant input on this lane.

    It is a membership question, and the answer is a 403 — never auth as the
    requested org, and never a silent downgrade to the caller's first
    membership (which would file A's rows under an org the request never asked
    for, and hide the attempt).

    Mutation this discriminates: dropping the `_session_pinned_org` gate in
    `_session_user_org` → a user who belongs only to A gets org B's tenant.
    """
    from fastapi import HTTPException

    from tortoise import hosted_api as ha

    cp = _two_tenant_cp()
    _wire(monkeypatch, cp)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            ha._session_user_org(_Request(ORG_B), {"user_id": USER_A_ONLY}))
    assert excinfo.value.status_code == 403, excinfo.value
    assert "membership" in str(excinfo.value.detail).lower(), excinfo.value.detail

    # The SAME user pinned to the org it does belong to still resolves — the
    # refusal is the pin's, not the user's.
    org = _session_org(monkeypatch, cp, user=USER_A_ONLY, pin=ORG_A)
    _assert_names_its_own(org, org_id=ORG_A, row_id=ROW_A,
                          other_org=ORG_B, other_row=ROW_B, label="member pin")


def test_a_tenantless_credential_is_never_resolved_into_the_other_tenant(
        monkeypatch):
    """The REFUSAL direction, alternated across the SAME two tenants.

    B resolves first, so any cached/"last seen"/process-global tenant is B's.
    Then A resolves on a control plane that cannot name A's default row — the
    absent/tombstoned/unreadable arm — and A must name NO tenant. Falling back
    to B's would be the cross-tenant record write this whole seam exists to
    prevent, and it is the one shape a positive-path test cannot see.

    Mutation this discriminates (measured): making `record_tenant_for`'s
    tenantless arm (`record_writer.py:793`) return a last-seen tenant leaves
    every other test in this module green — it reddens only here.
    """
    from tortoise import hosted_api as ha
    from tortoise import record_writer, supabase_control as sc

    cp = _two_tenant_cp()
    _wire(monkeypatch, cp)
    real = sc.default_graph_id_for_credential

    def _a_is_unnameable(control_plane, org_id, *a, **kw):
        if org_id == ORG_A:
            return None          # no active default row this lane can read
        return real(control_plane, org_id, *a, **kw)

    monkeypatch.setattr(sc, "default_graph_id_for_credential",
                        _a_is_unnameable)

    b = _session_org(monkeypatch, cp, pin=ORG_B)
    _assert_names_its_own(b, org_id=ORG_B, row_id=ROW_B,
                          other_org=ORG_A, other_row=ROW_A, label="B first")

    a = _session_org(monkeypatch, cp, pin=ORG_A)
    assert a["org_id"] == ORG_A            # the request still succeeds
    assert a["default_graph_id"] is None, (
        f"an unnameable default produced {a['default_graph_id']!r} — a guess, "
        "or another tenant's row")
    assert ha._resolve_record_tenant(a) is None, (
        "the tenantless credential resolved to a tenant — with B live in this "
        "process, that tenant is another org's, and the row it files is a "
        "cross-tenant write")
    assert record_writer.record_tenant_for(a, control_plane_ids=True) is None, (
        "record_tenant_for named a tenant for a credential that has none")


def test_negative_control_a_swapped_resolution_reddens_the_checker(monkeypatch):
    """The checker must be able to FAIL, or the three tests above prove nothing.

    This is the B5 exit-evidence rule the repository already applies to its
    cross-tenant read suite (`tests/test_cross_tenant_read_isolation.py`
    `test_negative_control_*`): break ONE seam — here the default-row
    resolution, made to hand org A the row that belongs to org B — and assert
    the very checker the green tests use goes RED.
    """
    from tortoise import supabase_control as sc

    cp = _two_tenant_cp()
    _wire(monkeypatch, cp)

    real = sc.default_graph_id_for_credential

    def _swapped(control_plane, org_id, *a, **kw):
        return ROW_B if org_id == ORG_A else real(control_plane, org_id, *a, **kw)

    monkeypatch.setattr(sc, "default_graph_id_for_credential", _swapped)

    a = _session_org(monkeypatch, cp, pin=ORG_A)
    assert a["default_graph_id"] == ROW_B, (
        "the negative control did not take effect — it must hand A the OTHER "
        "tenant's row, or it is not breaking the seam it claims to break")

    with pytest.raises(AssertionError):
        _assert_names_its_own(a, org_id=ORG_A, row_id=ROW_A,
                              other_org=ORG_B, other_row=ROW_B,
                              label="swapped")
