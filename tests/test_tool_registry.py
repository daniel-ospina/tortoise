"""Test tool registry: Gate 1 equivalence + adapter cutover tests."""
from __future__ import annotations

import asyncio

import pytest
from fastmcp import FastMCP
from fastmcp.tools import FunctionTool
from mcp.types import ToolAnnotations


class TestFastMCPAddToolSpike:
    """Gate 0: Validate FastMCP 3.4.6 add_tool(from_function(annotations=...))."""

    def test_from_function_with_annotations(self):
        """FunctionTool.from_function preserves annotations."""
        def my_tool(x: int) -> int:
            """A test tool."""
            return x + 1

        tool = FunctionTool.from_function(
            my_tool,
            name="my_tool",
            description="A test tool.",
            annotations=ToolAnnotations(
                readOnlyHint=True,
                idempotentHint=True,
                destructiveHint=False,
            ),
        )
        assert tool.name == "my_tool"
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.destructiveHint is False

    def test_add_tool_registers_in_list(self):
        """add_tool() makes the tool appear in _list_tools()."""
        async def _check():
            mcp = FastMCP("test_spike")
            mcp.add_tool(FunctionTool.from_function(
                lambda x: x,
                name="spike_echo",
                description="Echo tool for spike.",
                annotations=ToolAnnotations(readOnlyHint=True),
            ))
            tools = await mcp._list_tools()
            tool_names = [t.name for t in tools]
            assert "spike_echo" in tool_names
            # Verify annotations survive the round-trip
            spike = next(t for t in tools if t.name == "spike_echo")
            assert spike.annotations.readOnlyHint is True

        asyncio.run(_check())


class TestRegistryEquivalence:
    """Gate 1: Derived HTTP_ALLOWED == literal HTTP_ALLOWED."""

    def test_derived_http_allowed_equals_literal(self):
        """Every tool with http_policy=True must be in HTTP_ALLOWED, and vice versa."""
        from tortoise.tool_registry import TOOL_REGISTRY
        derived = frozenset(
            t.name for t in TOOL_REGISTRY if t.http_policy
        )
        from tortoise.mcp_auth import HTTP_ALLOWED
        assert derived == HTTP_ALLOWED, (
            f"Derived HTTP_ALLOWED mismatch:\n"
            f"  In derived but not literal: {derived - HTTP_ALLOWED}\n"
            f"  In literal but not derived: {HTTP_ALLOWED - derived}"
        )

    def test_registry_count(self):
        """58 tools — same count as @mcp.tool() decorators."""
        from tortoise.tool_registry import TOOL_REGISTRY
        assert len(TOOL_REGISTRY) == 58, f"Expected 58, got {len(TOOL_REGISTRY)}"

    def test_no_duplicate_names(self):
        """No two registry entries share the same name."""
        from tortoise.tool_registry import TOOL_REGISTRY
        names = [t.name for t in TOOL_REGISTRY]
        assert len(names) == len(set(names)), f"Duplicates: {[n for n in names if names.count(n) > 1]}"

    def test_http_policy_exclusions(self):
        """Known exclusions are http_policy=False."""
        from tortoise.tool_registry import TOOL_REGISTRY
        by_name = {t.name: t for t in TOOL_REGISTRY}
        excluded = {"tortoise_team_create", "tortoise_backfill_v25",
                     "tortoise_ingest_corpus", "tortoise_index_sessions"}
        for name in excluded:
            assert name in by_name, f"Missing tool: {name}"
            assert by_name[name].http_policy is False, f"{name} should be excluded"


class TestFastMCPAdapter:
    """Gate 2: MCP adapter emits correct tools from registry."""

    def test_adapter_registers_all_tools(self):
        """Every registry entry becomes a registered MCP tool."""
        async def _check():
            from tortoise.tool_registry import TOOL_REGISTRY, FastMCPAdapter
            from fastmcp import FastMCP

            mcp = FastMCP("test_adapter")
            adapter = FastMCPAdapter(mcp)
            # Build a handler map: tool_name → dummy function
            handlers = {}
            for entry in TOOL_REGISTRY:
                # Create a unique callable per tool (no **kwargs — FastMCP rejects it)
                def _make_handler(name=entry.name):
                    def _handler(x: int = 0) -> dict:
                        return {"tool": name}
                    return _handler
                handlers[entry.name] = _make_handler()

            adapter.register_all(TOOL_REGISTRY, handlers)

            tools = await mcp._list_tools()
            registered = {t.name for t in tools}
            expected = {t.name for t in TOOL_REGISTRY}
            missing = expected - registered
            assert not missing, f"Tools not registered: {missing}"
            extra = registered - expected
            assert not extra, f"Unexpected tools: {extra}"

        asyncio.run(_check())

    def test_adapter_preserves_annotations(self):
        """ToolAnnotations from registry appear on registered tools."""
        async def _check():
            from tortoise.tool_registry import TOOL_REGISTRY, FastMCPAdapter
            from fastmcp import FastMCP

            mcp = FastMCP("test_annotations")
            adapter = FastMCPAdapter(mcp)
            handlers = {}
            for entry in TOOL_REGISTRY:
                def _make_handler():
                    def _handler(x: int = 0) -> dict:
                        return {}
                    return _handler
                handlers[entry.name] = _make_handler()

            adapter.register_all(TOOL_REGISTRY, handlers)

            # Spot-check: create_point is readOnly=False, idempotentHint=True
            tools = await mcp._list_tools()
            by_name = {t.name: t for t in tools}
            cp = by_name["tortoise_create_point"]
            assert cp.annotations.readOnlyHint is False
            assert cp.annotations.idempotentHint is True
            assert cp.annotations.destructiveHint is False

            # Spot-check: tortoise_query is readOnly=True
            q = by_name["tortoise_query"]
            assert q.annotations.readOnlyHint is True

        asyncio.run(_check())

    def test_adapter_excluded_tool_still_registered(self):
        """Excluded tools (http_policy=False) are still registered in MCP."""
        async def _check():
            from tortoise.tool_registry import TOOL_REGISTRY, FastMCPAdapter
            from fastmcp import FastMCP

            mcp = FastMCP("test_excluded")
            adapter = FastMCPAdapter(mcp)
            handlers = {}
            for entry in TOOL_REGISTRY:
                def _make_handler():
                    def _handler(x: int = 0) -> dict:
                        return {}
                    return _handler
                handlers[entry.name] = _make_handler()

            adapter.register_all(TOOL_REGISTRY, handlers)

            tools = await mcp._list_tools()
            registered = {t.name for t in tools}
            # Excluded tools should still be registered (HTTP filter handles hiding them)
            assert "tortoise_team_create" in registered
            assert "tortoise_backfill_v25" in registered
            assert "tortoise_ingest_corpus" in registered
            assert "tortoise_index_sessions" in registered

        asyncio.run(_check())


class TestFastAPIRouterAdapter:
    """Gate 3: REST adapter generates correct routes from registry."""

    def test_adapter_registers_all_rest_routes(self):
        """Every registry entry with rest_spec becomes a route."""
        from tortoise.tool_registry import TOOL_REGISTRY, FastAPIRouterAdapter
        from fastapi import APIRouter

        router = APIRouter()
        adapter = FastAPIRouterAdapter(router)

        def _dummy():
            return {}

        handlers = {t.name: _dummy for t in TOOL_REGISTRY if t.rest_spec}
        adapter.register_all(TOOL_REGISTRY, handlers)

        route_paths = set()
        for r in router.routes:
            methods = sorted(m for m in getattr(r, "methods", set()) if m != "HEAD")
            if methods:
                route_paths.add((methods[0], getattr(r, "path", "")))

        assert ("POST", "/v1/points") in route_paths, route_paths
        assert ("POST", "/v1/dream") in route_paths
        assert ("GET", "/v1/search") in route_paths
        assert ("GET", "/v1/context") in route_paths
        # raw-Cypher ops NOT registered (no rest_spec) — drift documented
        assert ("GET", "/v1/sessions") not in route_paths
