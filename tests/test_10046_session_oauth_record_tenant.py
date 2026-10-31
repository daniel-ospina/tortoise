"""#10046: the SESSION/OAuth credential class installs a record tenant.

Measured at `origin/main` (`fffcc7c0c`) by driving the REAL resolvers with the
in-repo fake control plane: an org-wide API KEY names the record tenant
(`resolve_api_key` stamps `default_graph_id`), while a session/JWT credential
(`hosted_api._session_user_org`) and an OAuth `oat_` token
(`oauth.resolve_oauth_access_token`) carried no `default_graph_id` at all — so
`record_tenant_for` refused every armed write on those two classes `no_tenant`
while the data itself landed in the default graph. A whole credential class
invisible to the record, and silently.

These drive the same seam the product does — resolve the credential, then ask
the carrier the record actually reads — so a regression that drops the stamp
fails here instead of showing up as a `no_tenant` count on a deployment that
reads armed and green.
"""
import asyncio

from tests.fake_control_plane import FakeControlPlane

SESSION_ORG = "org-session-10046"
OAUTH_ORG = "org-oauth-10046"
HEAL_ORG = "org-heal-10046"
UNNAMEABLE_ORG = "org-unnameable-10046"
USER = "11111111-1111-1111-1111-111111111111"
DEFAULT_ROW_ID = "g_0123456789abcdef"
OAUTH_TOKEN = "oat_" + "b" * 40


def _org_row(org_id: str) -> dict:
    return {
        "id": org_id, "tier": "team", "graph_name": "org_" + org_id,
        "max_users": 5, "max_graphs": 3, "graph_size_cap": 1000,
        "max_points": 1000, "suspended_at": None, "flagged_at": None,
        "email": "owner@example.test", "dashboard_key_login": True,
        "backup_enabled": True,
    }


def _default_row(org_id: str) -> dict:
    return {
        "id": DEFAULT_ROW_ID, "org_id": org_id, "kind": "default",
        "status": "active", "namespace": "org_" + org_id, "name": "default",
    }


class _UnreadableGraphs:
    """A control plane whose `graphs` table raises — the fail-closed arm."""

    def __init__(self, inner: FakeControlPlane) -> None:
        self._inner = inner

    def query(self, table, *args, **kwargs):
        if table == "graphs":
            raise RuntimeError("graphs unreadable (fail-closed test)")
        return self._inner.query(table, *args, **kwargs)


def _wire(monkeypatch, cp, org_id: str):
    """Install `cp` as the control plane and stub the non-auth side effects."""
    from tortoise import hosted_api as ha
    from tortoise import supabase_control as sc

    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(sc, "get_control_plane", lambda: cp)
    monkeypatch.setattr(ha, "_org_node_sync_limits", lambda oid: {})

    async def _noop(*a, **kw):
        return None

    monkeypatch.setattr(ha, "_abuse_post_auth", _noop)
    return cp


def _session_cp(*, with_default_row: bool = True, org_id: str = SESSION_ORG):
    fake = FakeControlPlane()
    fake.seed("organizations", [_org_row(org_id)])
    if with_default_row:
        fake.seed("graphs", [_default_row(org_id)])
    fake.seed("org_memberships", [{
        "user_id": USER, "org_id": org_id, "role": "owner", "status": "active",
    }])
    return fake


class _Request:
    def __init__(self) -> None:
        self.query_params: dict = {}
        self.state = type("S", (), {})()
        self.headers: dict = {}


def _session_org(monkeypatch, cp, org_id: str = SESSION_ORG) -> dict:
    from tortoise import hosted_api as ha

    _wire(monkeypatch, cp, org_id)
    return asyncio.run(ha._session_user_org(_Request(), {"user_id": USER}))


def _oauth_org(monkeypatch, cp, org_id: str = OAUTH_ORG) -> dict:
    from tortoise import oauth

    cp.seed("oauth_access_tokens", [{
        "token_hash": oauth._sha256(OAUTH_TOKEN), "client_id": "client-10046",
        "user_id": USER, "org_id": org_id, "scope": "mcp",
        "expires_at": "2099-01-01T00:00:00+00:00", "revoked_at": None,
    }])
    _wire(monkeypatch, cp, org_id)
    return oauth.resolve_oauth_access_token(cp, OAUTH_TOKEN)


def test_a_session_request_installs_the_default_graphs_record_tenant(monkeypatch):
    """A session/JWT caller is a real product user: it must file a record row.

    Mutation this discriminates: deleting the `org["default_graph_id"] = ...`
    stamp in `hosted_api._session_user_org` → `bound` stays empty and
    `current_record_tenant()` is None (the measured pre-fix state, where the
    write was refused `no_tenant` while the data went to the default graph).
    """
    from tortoise import hosted_api as ha
    from tortoise import mcp_auth, record_writer

    org = _session_org(monkeypatch, _session_cp())
    assert org["default_graph_id"] == DEFAULT_ROW_ID

    # The REST lane's carrier: the SDK the request's write runs on.
    bound: list[tuple[str, str]] = []

    class _FakeSDK:
        def _bind_record_tenant(self, org_id, graph_id):
            bound.append((org_id, graph_id))

    monkeypatch.setattr(ha, "_make_sdk", lambda **kw: _FakeSDK())
    ha._data_sdk(org)
    assert bound == [(SESSION_ORG, DEFAULT_ROW_ID)], bound

    # The MCP lane's carrier, on the SAME credential dict: one answer.
    with mcp_auth._record_tenant_scope(org, control_plane_ids=True):
        assert record_writer.current_record_tenant() == (SESSION_ORG,
                                                         DEFAULT_ROW_ID)


def test_an_oauth_token_installs_the_default_graphs_record_tenant(monkeypatch):
    """The `oat_` class never goes through `resolve_api_key` — and now names
    its tenant anyway, through the same control-plane read.

    Mutation this discriminates: deleting the `org["default_graph_id"] = ...`
    assignment in `oauth.resolve_oauth_access_token` → both assertions below
    see None (the pre-fix state: every OAuth write refused `no_tenant`).
    """
    from tortoise import hosted_api as ha
    from tortoise import mcp_auth, record_writer

    cp = FakeControlPlane()
    cp.seed("organizations", [_org_row(OAUTH_ORG)])
    cp.seed("graphs", [_default_row(OAUTH_ORG)])
    org = _oauth_org(monkeypatch, cp)

    assert org["default_graph_id"] == DEFAULT_ROW_ID
    assert ha._resolve_record_tenant(org) == (OAUTH_ORG, DEFAULT_ROW_ID)
    with mcp_auth._record_tenant_scope(org, control_plane_ids=True):
        assert record_writer.current_record_tenant() == (OAUTH_ORG,
                                                         DEFAULT_ROW_ID)


def test_a_missed_provisioning_mint_is_healed_on_the_session_path(monkeypatch):
    """The session lane resolves through the SAME self-healing home as an API
    key, so an org the migration/backfill missed is not refused forever.

    Mutation this discriminates: replacing the
    `default_graph_id_for_credential` call with a bare `default_graph_id_for`
    read → the org stays unnamed, `default_graph_id` is None, and its record
    writes are refused `no_tenant` for good (the #10025 F1 window).
    """
    from tortoise import supabase_control as sc

    cp = _session_cp(with_default_row=False, org_id=HEAL_ORG)
    org = _session_org(monkeypatch, cp, HEAL_ORG)

    minted = org["default_graph_id"]
    assert isinstance(minted, str) and minted.startswith("g_"), minted
    assert minted == sc.default_graph_id_for(cp, HEAL_ORG)  # persisted, not just returned


def test_an_unnameable_default_is_still_refused_not_guessed(monkeypatch):
    """Fail-closed survives the wiring: no readable default row ⇒ no tenant.

    A guess here is a cross-tenant record write. Mutation: making the resolver
    fall back to a constant / the `"default"` sentinel → this returns a tuple
    and the test reddens.
    """
    from tortoise import hosted_api as ha

    cp = _UnreadableGraphs(_session_cp(org_id=UNNAMEABLE_ORG))
    org = _session_org(monkeypatch, cp, UNNAMEABLE_ORG)

    assert org["org_id"] == UNNAMEABLE_ORG          # the request still succeeds
    assert org["default_graph_id"] is None
    assert ha._resolve_record_tenant(org) is None  # ⇒ counted `no_tenant`


def test_an_offload_that_delivered_no_result_is_not_a_tenant(monkeypatch):
    """An OFFLOAD refusal is not a tenant id, and must not wear one's name.

    `best_effort=True` returns the ``OFFLOAD_REFUSED`` sentinel (NOT ``None``)
    when the submission is refused or its wait bound expires, so the raw seam
    result must be normalised before it is stored in a field called
    `default_graph_id`. `record_tenant_for` type-checks ``str``, so the refusal
    direction already holds — the hazard is the NEXT reader, which would inherit
    the sentinel and could file a row under it.

    Mutation this discriminates: dropping the ``isinstance(..., str)``
    normalisation in `_session_user_org` → `org["default_graph_id"]` is the
    sentinel and the first assertion reddens.
    """
    from tortoise import hosted_api as ha

    _wire(monkeypatch, _session_cp(), SESSION_ORG)

    async def _undelivered(fn, **kwargs):
        if kwargs.get("op") == "default_graph_row":
            return ha.OFFLOAD_REFUSED
        return fn()  # the other reads on this lane are not what is under test

    monkeypatch.setattr(ha, "_cp_offload", _undelivered)
    org = asyncio.run(ha._session_user_org(_Request(), {"user_id": USER}))

    assert org["default_graph_id"] is None          # never the sentinel
    assert ha._resolve_record_tenant(org) is None   # ⇒ counted `no_tenant`
