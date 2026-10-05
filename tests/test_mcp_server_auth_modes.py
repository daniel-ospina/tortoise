"""Tests for create_http_app auth_mode param (#338 T1.1).

Three modes:
  "tenant" (default) — TeamResolutionMiddleware, registry tt_ keys (hosted, byte-identical)
  "static"           — StaticKeyMiddleware, single TORTOISE_API_KEY (self-host LAN)
  "none"             — no auth middleware (localhost-bound self-host eval)

Mirrors test_mcp_http.py's mounted-TestClient pattern. Embedded FalkorDBLite.
"""
from __future__ import annotations

import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")


import pytest

from tortoise.mcp_server import create_http_app


def _mcp_post(tc, payload, auth_header=None):
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if auth_header:
        headers["Authorization"] = auth_header
    return tc.post("/mcp", json=payload, headers=headers)


def _initialize_payload():
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "auth-modes-test", "version": "0"},
        },
    }


def _mounted_test_client(app):
    """Wrap the MCP app in a Starlette Mount at /mcp (mirrors test_mcp_http)."""
    from contextlib import asynccontextmanager  # noqa: I001
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from starlette.testclient import TestClient

    @asynccontextmanager
    async def _lifespan(parent_app):
        async with app.lifespan(app):
            yield

    parent = Starlette(lifespan=_lifespan, routes=[Mount("/mcp", app=app)])
    return TestClient(parent)


@pytest.fixture
def make_client():
    """Factory returning an entered TestClient for a given auth_mode."""
    created = []

    def _make(auth_mode="tenant", api_key=None, allowed_origins=None,
             tool_group=None):
        app = create_http_app(
            allowed_origins=allowed_origins or ["http://localhost:8000"],
            auth_mode=auth_mode,
            api_key=api_key,
            tool_group=tool_group,
        )
        tc = _mounted_test_client(app)
        tc.__enter__()
        created.append(tc)
        return tc

    yield _make
    for tc in created:
        try:  # noqa: SIM105
            tc.__exit__(None, None, None)
        except Exception:
            pass


class TestTenantModeDefault:
    """auth_mode default "tenant" = hosted byte-identical: tt_/tk_ keys
    (API_KEY_PREFIXES) are the accepted credential family."""

    def test_missing_bearer_401(self, make_client):
        tc = make_client()
        r = _mcp_post(tc, _initialize_payload())
        assert r.status_code == 401

    def test_wrong_prefix_401(self, make_client):
        tc = make_client()
        r = _mcp_post(tc, _initialize_payload(), auth_header="Bearer abc_123")
        assert r.status_code == 401

    def test_tk_key_resolves_tenant_mode(self, tmp_path, monkeypatch):
        """C2 #2111 (code-review P2): a tk_ per-graph key authenticates
        through TeamResolutionMiddleware in tenant mode — the format gate
        accepts API_KEY_PREFIXES (tt_/tk_) and the registry resolves the
        key. Regression: dropping tk_ from the tuple would 401 here."""
        from tortoise.mcp_server import create_http_app
        from tortoise.sdk import TortoiseSDK
        db_path = str(tmp_path / "tk-mcp.db")
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        reg = TortoiseSDK(db_path=db_path, namespace="registry")
        team = reg.org_create("tk-mcp-team")
        # Mint a tk_ key via the C2 prefix kwarg (the provisioning lane's
        # credential class).
        key = reg.apikey_create(team["id"], "t", prefix="tk_")["api_key"]
        assert key.startswith("tk_")
        app = create_http_app(allowed_origins=[], _registry_sdk=reg)
        tc = _mounted_test_client(app)
        with tc:
            r = _mcp_post(tc, _initialize_payload(),
                          auth_header=f"Bearer {key}")
            # Auth passes → the MCP server proceeds (200/202), not 401
            assert r.status_code in (200, 202), r.text
            # A tt_ key on the same registry also resolves (unchanged)
            key2 = reg.apikey_create(team["id"], "t")["api_key"]
            r2 = _mcp_post(tc, _initialize_payload(),
                           auth_header=f"Bearer {key2}")
            assert r2.status_code in (200, 202), r2.text

    def test_minted_deleg0_key_403_over_mcp(self, tmp_path, monkeypatch):
        """C2 #2111 (code-review security P1, #2b): a MINTED deleg=0 per-
        graph key must NOT drive MCP tools — MCP tools run on the
        team-scoped SDK (whole-team namespace) with no per-graph scope
        enforcement until C5 #2114. The REST surface gates deleg=0 on
        management + writes; MCP must not be the fail-open lane where a
        handed-out least-privilege key gets every write tool against all
        team data. 403 (minted keys dormant), never 200/202."""
        from tortoise.mcp_server import create_http_app
        from tortoise.sdk import TortoiseSDK
        db_path = str(tmp_path / "tk-mcp-deleg.db")
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        reg = TortoiseSDK(db_path=db_path, namespace="registry")
        team = reg.org_create("tk-mcp-deleg-team")
        # deleg=0 minted per-graph key (the provisioning lane's credential
        # class — C2 _mint_graph_key stamps delegation_depth 0).
        key = reg.apikey_create(team["id"], "t", prefix="tk_",
                                delegation_depth=0)["api_key"]
        assert key.startswith("tk_")
        app = create_http_app(allowed_origins=[], _registry_sdk=reg)
        tc = _mounted_test_client(app)
        with tc:
            r = _mcp_post(tc, _initialize_payload(),
                          auth_header=f"Bearer {key}")
            assert r.status_code == 403, r.text
            assert "Minted keys cannot be used over MCP" in r.text, r.text


class TestStaticMode:
    """auth_mode="static": single API key, constant-time compare."""

    def test_missing_key_401(self, make_client):
        tc = make_client(auth_mode="static", api_key="secret-key")
        r = _mcp_post(tc, _initialize_payload())
        assert r.status_code == 401

    def test_wrong_key_401(self, make_client):
        tc = make_client(auth_mode="static", api_key="secret-key")
        r = _mcp_post(tc, _initialize_payload(), auth_header="Bearer wrong-key")
        assert r.status_code == 401

    def test_correct_key_allowed(self, make_client):
        tc = make_client(auth_mode="static", api_key="secret-key")
        r = _mcp_post(tc, _initialize_payload(), auth_header="Bearer secret-key")
        # Auth passes — MCP server proceeds (200/202), not 401
        assert r.status_code in (200, 202)

    def test_none_key_fails_closed_503(self, make_client):
        tc = make_client(auth_mode="static", api_key=None)
        r = _mcp_post(tc, _initialize_payload(), auth_header="Bearer anything")
        assert r.status_code == 503


class TestNoneMode:
    """auth_mode="none": no auth middleware — POSTs pass through."""

    def test_no_auth_allowed(self, make_client):
        tc = make_client(auth_mode="none")
        r = _mcp_post(tc, _initialize_payload())
        # No auth layer — MCP server responds (200/202), not 401
        assert r.status_code in (200, 202)


class TestMetadataRouteUnauthenticated:
    """GET / metadata route never requires auth in any mode."""

    def test_get_metadata_static_mode(self, make_client):
        tc = make_client(auth_mode="static", api_key="secret-key")
        assert tc.get("/mcp").status_code == 200

    def test_get_metadata_none_mode(self, make_client):
        tc = make_client(auth_mode="none")
        assert tc.get("/mcp").status_code == 200


class TestToolGroupFiltering:
    """Role-scoped server (#523): tool_group filters tools/list."""

    def _list_tool_names(self, tc):
        import json as _json
        r = tc.post("/mcp",
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                    headers={"Accept": "application/json, text/event-stream",
                             "Content-Type": "application/json"})
        # Streamable HTTP returns SSE: "event: message\r\ndata: {...}"
        data_line = next((ln[6:] for ln in r.text.splitlines() if ln.startswith("data: ")), r.text)
        body = _json.loads(data_line)
        return [t["name"] for t in body.get("result", {}).get("tools", [])]

    def test_group_memory_lists_only_memory_tools(self, make_client):
        # The oracle is the group the registry APPLIED to the entry — the same
        # single lookup the server filter reads (#3877). It used to be
        # `GROUP_BY_NAME.get(n)`, which asserts `served ⊆ map`: true only while the
        # map happens to list every served name, and never a statement about the
        # served group. The 8 names the map does not list are served by their
        # applied group, so that subset assertion pinned the very disagreement
        # #3877 is about.
        from tortoise.tool_registry import get_tool_by_name

        entries = get_tool_by_name()
        tc = make_client(auth_mode="none", tool_group="memory")
        names = self._list_tool_names(tc)
        assert names, "expected tools"
        assert all(entries[n].group == "memory" for n in names)
        assert len(names) <= 24  # memory group size (grew with #888/#913 consolidation train; #939)

    def test_group_scoped_server_serves_every_declared_member(self, make_client):
        """#3877 regression: a group-scoped server advertises EVERY member the
        registry assigns to that group and marks HTTP-eligible.

        The defect was that the filter re-derived the group from `GROUP_BY_NAME`
        with no default, so the 8 names the map does not list resolved to None and
        were dropped from `tools/list` on EVERY group-scoped server — while the
        registry had already assigned them "memory". Reachability is asserted on
        the real path (a scoped server's advertised surface), not on the map.
        """
        from tortoise.tool_registry import TOOL_REGISTRY

        declared = {t.name for t in TOOL_REGISTRY if t.group == "memory" and t.http_policy}
        assert declared, "the memory group must be non-empty"
        tc = make_client(auth_mode="none", tool_group="memory")
        served = set(self._list_tool_names(tc))
        missing = sorted(declared - served)
        assert not missing, (
            f"declared memory tools not served on a memory-scoped server: {missing}"
        )

    def test_no_group_lists_all_http_tools(self, make_client):
        tc = make_client(auth_mode="none")
        names = self._list_tool_names(tc)
        assert len(names) > 30  # full surface when no group filter


def _lifespan_for(app):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _lifespan(parent_app):
        async with app.lifespan(app):
            yield
    return _lifespan
