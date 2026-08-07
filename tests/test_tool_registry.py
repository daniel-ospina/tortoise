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
