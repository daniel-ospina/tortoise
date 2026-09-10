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

import contextlib

import pytest

from tests._live_utils import live_uri
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
        team = reg.team_create("tk-mcp-team")
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
        team = reg.team_create("tk-mcp-deleg-team")
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
        from tortoise.tool_registry import GROUP_BY_NAME

        tc = make_client(auth_mode="none", tool_group="memory")
        names = self._list_tool_names(tc)
        assert names, "expected tools"
        assert all(GROUP_BY_NAME.get(n) == "memory" for n in names)
        assert len(names) <= 24  # memory group size (grew with #888/#913 consolidation train; #939)

    def test_no_group_lists_all_http_tools(self, make_client):
        tc = make_client(auth_mode="none")
        names = self._list_tool_names(tc)
        assert len(names) > 30  # full surface when no group filter


class TestAskExposureGating:
    """#2013 PRODUCT-GATING: the MCP tortoise_ask tool is absent from the
    DEFAULT hosted surface unless TORTOISE_ENABLE_ASK=1, and present on an
    EXPLICIT tool_group="ask" server (dev/eval opt-in). The reader ships;
    only the ask EXPOSURE is gated."""

    _list_tool_names = TestToolGroupFiltering._list_tool_names

    def _call_tool(self, tc, name, arguments):
        """Issue a JSON-RPC tools/call and return the parsed body (SSE-framed
        responses on Streamable HTTP — mirrors _list_tool_names)."""
        import json as _json
        r = tc.post("/mcp",
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": name, "arguments": arguments}},
                    headers={"Accept": "application/json, text/event-stream",
                             "Content-Type": "application/json"})
        data_line = next((ln[6:] for ln in r.text.splitlines()
                          if ln.startswith("data: ")), r.text)
        return _json.loads(data_line)

    @staticmethod
    def _result_text(body):
        """Concatenated tool-result content text (the _http_excluded_error
        body is serialized INTO the result content, per test_mcp_http.py's
        tortoise_dream excluded-call pattern)."""
        return "".join(c.get("text", "") for c in
                        body.get("result", {}).get("content", [])
                        if isinstance(c, dict))

    def test_ask_absent_from_default_surface(self, make_client, monkeypatch):
        monkeypatch.delenv("TORTOISE_ENABLE_ASK", raising=False)
        tc = make_client(auth_mode="none")
        names = self._list_tool_names(tc)
        assert "tortoise_ask" not in names
        # the default surface stays FULL otherwise — ~80 tools (88
        # http_policy − 1 gated ask − 7 no-handler registry skips − ~6
        # retired onboarding). A filter regression hiding a substantial
        # fraction fails this tight bound (test-review #2013 — the old
        # `> 30` guard could not).
        assert len(names) >= 75, f"default surface shrank to {len(names)}"

    def test_ask_present_with_explicit_ask_group(self, make_client, monkeypatch):
        # deliberate opt-in: an explicit tool_group="ask" server serves it
        # regardless of the exposure flag (this is the dev/eval surface)
        monkeypatch.delenv("TORTOISE_ENABLE_ASK", raising=False)
        tc = make_client(auth_mode="none", tool_group="ask")
        names = self._list_tool_names(tc)
        assert "tortoise_ask" in names
        assert set(names) == {"tortoise_ask"}

    def test_ask_present_on_default_surface_when_flag_on(self, make_client, monkeypatch):
        # TORTOISE_ENABLE_ASK=1 unlocks the ask tool on the default surface
        # (parity with the /v1/ask route)
        monkeypatch.setenv("TORTOISE_ENABLE_ASK", "1")
        tc = make_client(auth_mode="none")
        names = self._list_tool_names(tc)
        assert "tortoise_ask" in names

    def test_ask_present_with_explicit_group_and_flag_on(self, make_client, monkeypatch):
        # flag-ON + explicit group combination (the dev surface with the
        # exposure unlocked) — serves the ask tool either way
        monkeypatch.setenv("TORTOISE_ENABLE_ASK", "1")
        tc = make_client(auth_mode="none", tool_group="ask")
        names = self._list_tool_names(tc)
        assert set(names) == {"tortoise_ask"}

    def test_ask_call_gated_off_default_surface(self, make_client, monkeypatch):
        """#2013 CALL-TIME gate: a NAME-ADDRESSED tools/call for
        tortoise_ask on the default hosted surface (flag unset) returns the
        ERR_EXCLUDED structured error. FastMCP dispatches tools/call by
        name WITHOUT consulting the list Transform, so a listing-only gate
        would leak the ask exposure; the call-time gate mirrors the listing
        intent at the call boundary."""
        monkeypatch.delenv("TORTOISE_ENABLE_ASK", raising=False)
        tc = make_client(auth_mode="none")
        body = self._call_tool(tc, "tortoise_ask", {"question": ""})
        text = self._result_text(body)
        assert "-32004" in text, \
            f"expected ERR_EXCLUDED, got: {body}"
        # the ask-gate message is gated-specific (the default
        # _http_excluded_error text's "hosted REST API" hint would be wrong
        # — /v1/ask is gated off too, #2013)
        assert "gated off (#2013)" in text, \
            f"expected the ask-gated message, got: {body}"
        # the pipeline must NOT run — local-lane validation (invalid_question)
        # would prove the gate was skipped
        assert "invalid_question" not in text, \
            f"pipeline ran — the call-time gate did not fire: {body}"

    def test_ask_call_reaches_pipeline_when_flag_on(self, make_client, monkeypatch):
        """TORTOISE_ENABLE_ASK=1: the default-surface call reaches the
        pipeline — an EMPTY question hits local-lane validation
        (invalid_question), proving the gate passed the call through."""
        monkeypatch.setenv("TORTOISE_ENABLE_ASK", "1")
        tc = make_client(auth_mode="none")
        body = self._call_tool(tc, "tortoise_ask", {"question": ""})
        text = self._result_text(body)
        assert "not available over HTTP" not in text, \
            f"call-time gate blocked an unlocked surface: {body}"
        assert "invalid_question" in text, \
            f"expected validation error (pipeline reached), got: {body}"

    def test_ask_call_serves_on_explicit_ask_group_flag_off(self, make_client, monkeypatch):
        """An explicit tool_group='ask' server (dev/eval opt-in) serves the
        call even with the flag OFF — the call-time gate must not block the
        documented opt-in surface."""
        monkeypatch.delenv("TORTOISE_ENABLE_ASK", raising=False)
        tc = make_client(auth_mode="none", tool_group="ask")
        body = self._call_tool(tc, "tortoise_ask", {"question": ""})
        text = self._result_text(body)
        assert "not available over HTTP" not in text, \
            f"call-time gate blocked the explicit ask group: {body}"
        assert "invalid_question" in text, \
            f"expected validation error (pipeline reached), got: {body}"

    def test_ask_strict_flag_parse_off(self, make_client, monkeypatch):
        # the MCP gate parses STRICTLY == "1" — "0"/"true" keep the ask
        # tool gated off the default surface (parity with the route)
        for flag in ("0", "true"):
            monkeypatch.setenv("TORTOISE_ENABLE_ASK", flag)
            tc = make_client(auth_mode="none")
            names = self._list_tool_names(tc)
            assert "tortoise_ask" not in names, flag


class TestAskConnectedAssemblyExposure:
    """#2165 Task 6: MCP tortoise_ask exposure inherits the connected-
    assembly branch via the in-process sdk.ask() — flags ON + fired shape →
    ASSEMBLED evidence in the tool result; a forced assembler-stage raise
    maps to the retrieval-unavailable code (never a raw 500/traceback).

    auth_mode="none" sets _current_team_id="selfhost" → the ask tool opens
    TortoiseSDK(namespace="selfhost") (graph team_selfhost on the env URI),
    so the fixture is seeded INTO that namespace graph.

    Docker-gated at CALL time (this module is embedded by default in the
    carve-out CI lane — no docker probe at import)."""

    _call_tool = TestAskExposureGating._call_tool

    @staticmethod
    def _result_text(body):
        return "".join(c.get("text", "") for c in
                       body.get("result", {}).get("content", [])
                       if isinstance(c, dict))

    def _run(self, monkeypatch, question, *, poison=False):
        import json as _json
        import uuid

        import tortoise.assembly as amod
        import tortoise.embeddings as _emb
        import tortoise.sdk as sdk_mod
        from tortoise.sdk import TortoiseSDK
        base = live_uri()
        uri = f"{base}_{uuid.uuid4().hex[:10]}"
        monkeypatch.setenv("TORTOISE_DB_URI", uri)
        # hermetic embedder: monkeypatch-scoped so the process-wide module
        # state is RESTORED at test teardown (raw assignment here leaked
        # EmbeddingModel.get/compute_embedding into every later test in the
        # pytest process — search_engine/session_semantic/session-embedding
        # reds after this class ran). Mirrors test_assembly_sdk's
        # _no_embedder(monkeypatch) pattern.
        monkeypatch.setattr(_emb, "compute_embedding",
                            staticmethod(lambda content: None))
        monkeypatch.setattr(_emb.EmbeddingModel, "get",
                            staticmethod(lambda: None))
        # probe FTS on the server first (skip when docker unavailable)
        from tests.test_ask_sdk import FakeReader
        from tortoise.sdk import TortoiseSDK as _PSDK
        try:
            ps = _PSDK()
            ps.create_point("statement", "probe zzqfulltext 7f3a9c",
                            id="pProbe", session_id="sess-p",
                            is_episodic=True, status="draft")
            hits = ps.tortoise_fts_query("zzqfulltext 7f3a9c",
                                         entity_type="point", limit=2)
            ok = bool(hits and hits[0].get("id") == "pProbe")
            with contextlib.suppress(Exception):
                ps._get_proj().db.select_graph(uri.rsplit("/", 1)[-1]).delete()
            ps.close()
        except Exception:
            ok = False
        if not ok:
            pytest.skip("requires TORTOISE_DB_URI (live FalkorDB FTS lane — tier-2 embedded legs skip)")
        # seed into the SELFHOST namespace graph (what the none-mode ask
        # tool reads)
        import tests._assembly_graph as ag
        s = TortoiseSDK(namespace="selfhost")
        try:
            ag.build_base_graph(s)
            # hermetic reader + assembly flag
            monkeypatch.setenv("TORTOISE_ASK_CONNECTED_ASSEMBLY", "1")
            monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory",
                                lambda: FakeReader(reply="GOLD", tokens_out=6))
            if poison:
                def _boom(question, *a, **k):
                    raise RuntimeError("stage exploded")
                monkeypatch.setattr(amod, "classify_question", _boom)
            from tortoise.mcp_server import create_http_app
            app = create_http_app(
                allowed_origins=["http://localhost:8000"],
                auth_mode="none", tool_group="ask")
            with _mounted_test_client(app) as tc:
                r = _mcp_post(tc, {
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "tortoise_ask",
                               "arguments": {"question": question,
                                             "question_date": "2026-09-10"}}},
                    auth_header=None)
            data_line = next(
                (ln[6:] for ln in r.text.splitlines()
                 if ln.startswith("data: ")), r.text)
            return _json.loads(data_line)
        finally:
            with contextlib.suppress(Exception):
                s._get_proj().db.select_graph("team_selfhost").delete()
            with contextlib.suppress(Exception):
                s.close()
            # the hermetic FakeReader was cached under ask:selfhost (the
            # per-namespace reader cache) — without a reset it leaks into
            # later selfhost/ask tests (selfhost_rest got 'GOLD' instead of
            # its own fake). Mirrors test_ask_sdk's autouse _clean_ask_state.
            with contextlib.suppress(Exception):
                from tortoise.sdk import _reset_ask_reader_cache_for_tests
                _reset_ask_reader_cache_for_tests()

    def test_mcp_ask_fired_assembled_evidence(self, monkeypatch):
        body = self._run(
            monkeypatch, "what is the current status of the couch?")
        text = self._result_text(body)
        assert "STATE (couch): superseded by sofa on 2026-09-01" in text, text
        assert "SUPERSEDED BY: sofa" in text, text

    def test_mcp_ask_stage_raise_maps_retrieval_unavailable(self, monkeypatch):
        body = self._run(
            monkeypatch, "what is the current status of the couch?",
            poison=True)
        text = self._result_text(body)
        assert "retrieval" in text.lower(), text
        assert "Traceback" not in text and "500" not in text, text


def _lifespan_for(app):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _lifespan(parent_app):
        async with app.lifespan(app):
            yield
    return _lifespan
