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
