"""#3883 — a retired MCP name must RESOLVE and WARN, never silently disappear.

#3836 option (b): a retired name keeps working and tells the caller it is
retired. These tests pin both halves of that contract on the real declaration:

* the name is OFF the advertised surface (it is a reduction), and
* the name still RESOLVES through the warning shim, and the warning reaches the
  caller in the answer (`content`) and in the result `_meta`.

They also pin the properties that make the retirement SAFE — the handler
function and the SDK method stay, and the shim's structured output is exactly
what the live tool produced.
"""
from __future__ import annotations

import asyncio

import pytest
from fastmcp.tools.base import ToolResult

from tortoise import mcp_server as ms
from tortoise.tool_registry import (
    RETIRED_TOOL_REGISTRY,
    RETIRED_USE_INSTEAD,
    TOOL_REGISTRY,
)

RETIRED_BY_NAME = {t.name: t for t in RETIRED_TOOL_REGISTRY}


class TestDeclaration:
    def test_retired_names_are_not_in_the_live_registry(self):
        live = {t.name for t in TOOL_REGISTRY}
        retired = set(RETIRED_BY_NAME)
        assert retired == set(RETIRED_USE_INSTEAD)
        assert not (live & retired), f"live and retired overlap: {live & retired}"

    def test_every_retired_entry_names_a_replacement(self):
        for name, spec in RETIRED_BY_NAME.items():
            assert spec.retired_use_instead, f"{name} retired without a replacement"
            assert spec.retired_use_instead.startswith("tortoise_"), spec.retired_use_instead

    def test_retired_handler_functions_still_exist(self):
        """The capability is not lost: the consolidator calls these interns."""
        for spec in RETIRED_TOOL_REGISTRY:
            handler = getattr(ms, spec.name, None)
            assert callable(handler), f"handler {spec.name}() was removed"

    def test_public_sdk_surface_is_unchanged(self):
        """Only MCP tool NAMES are retired — every public SDK method stays."""
        from tortoise.sdk import TortoiseSDK

        public = [
            n
            for n in dir(TortoiseSDK)
            if not n.startswith("_") and callable(getattr(TortoiseSDK, n))
        ]
        assert len(public) == 150, f"SDK surface changed: {len(public)} != 150"

    def test_retired_sdk_bindings_that_resolve_are_still_public(self):
        from tortoise.sdk import TortoiseSDK

        for spec in RETIRED_TOOL_REGISTRY:
            declared = spec.sdk_method or ""
            if declared and hasattr(TortoiseSDK, declared):
                assert callable(getattr(TortoiseSDK, declared)), declared


class TestAdvertisedSurface:
    def test_retired_names_are_not_advertised(self):
        """Both enumerations the surface-guard reads must exclude retired names."""

        async def _check():
            retired = set(RETIRED_BY_NAME)
            listed = {t.name for t in await ms.mcp._list_tools()}
            advertised = {t.name for t in await ms.mcp.list_tools()}
            assert not (listed & retired), f"components: {listed & retired}"
            assert not (advertised & retired), f"tools/list: {advertised & retired}"
            assert len(listed) == len(TOOL_REGISTRY)

        asyncio.run(_check())

    def test_every_retired_name_resolves_with_a_retirement_marker(self):
        """A retired name must never be a bare 'tool not found' (#3883)."""

        async def _check():
            for spec in RETIRED_TOOL_REGISTRY:
                tool = await ms.mcp.get_tool(spec.name)
                assert tool is not None, f"{spec.name} does not resolve"
                marker = ((tool.meta or {}).get("tortoise") or {}).get("retired") or {}
                assert marker.get("retired") is True, f"{spec.name} has no marker"
                assert marker.get("use_instead") == spec.retired_use_instead
                # The resolved implementation is the SHIM, not the original handler.
                assert (getattr(tool.fn, "__doc__", "") or "").startswith("RETIRED")

        asyncio.run(_check())

    def test_retired_names_have_no_registered_component(self):
        components = {
            getattr(c, "name", None)
            for k, c in ms.mcp._local_provider._components.items()
            if k.startswith("tool:")
        }
        assert not (components & set(RETIRED_BY_NAME))


class TestWarning:
    def test_warning_is_appended_to_content_and_in_meta(self):
        spec = RETIRED_BY_NAME["tortoise_list_tags"]
        base = ToolResult(content={"ok": True}, structured_content={"ok": True})
        out = ms._warn_retired_result(base, spec)
        assert out is not base
        # The warning is the LAST block: the payload stays content[0] and the
        # structured content is byte-identical to the live tool's.
        assert out.content[-1].text == ms._retired_warning(spec)["message"]
        assert out.content[0].text == base.content[0].text
        assert "tortoise_list_tags" in out.content[-1].text
        assert "tortoise_overview" in out.content[-1].text
        assert out.meta["tortoise"]["retired"]["use_instead"] == spec.retired_use_instead
        assert out.structured_content == {"ok": True}

    def test_shim_preserves_the_wrap_envelope_for_list_results(self):
        spec = RETIRED_BY_NAME["tortoise_list_tags"]

        def handler() -> list[dict]:
            return [{"a": 1}]

        shim = ms.build_retired_tools([spec], {spec.name: handler})[spec.name]
        result = asyncio.run(shim.run({}))
        assert result.structured_content == {"result": [{"a": 1}]}
        assert result.content[0].text.startswith("["), "payload must stay content[0]"
        assert result.content[-1].text.startswith("RETIRED TOOL")
        assert len(result.content) > 1, "the warning block is missing"

    def test_shim_preserves_dict_structured_content(self):
        spec = RETIRED_BY_NAME["tortoise_health"]

        def handler() -> dict:
            return {"status": "ok"}

        shim = ms.build_retired_tools([spec], {spec.name: handler})[spec.name]
        result = asyncio.run(shim.run({}))
        assert result.structured_content == {"status": "ok"}
        assert result.content[-1].text.startswith("RETIRED TOOL")
        assert result.content[0].text != result.content[-1].text

    def test_each_shim_calls_its_own_handler(self):
        """Regression: a loop-local `def` once captured the LAST handler, so every
        retired name answered with the last one's payload."""
        s1 = RETIRED_BY_NAME["tortoise_list_tags"]
        s2 = RETIRED_BY_NAME["tortoise_health"]
        calls: list[str] = []

        def h1():
            calls.append("h1")
            return {"who": "one"}

        def h2():
            calls.append("h2")
            return {"who": "two"}

        shims = ms.build_retired_tools([s1, s2], {s1.name: h1, s2.name: h2})
        r1 = asyncio.run(shims[s1.name].run({}))
        r2 = asyncio.run(shims[s2.name].run({}))
        assert calls == ["h1", "h2"]
        assert r1.structured_content == {"who": "one"}
        assert r2.structured_content == {"who": "two"}

    def test_transform_prefers_a_live_tool_over_a_shim(self):
        """If a name is live again, the live tool wins — the shim is a fallback."""
        s1 = RETIRED_BY_NAME["tortoise_list_tags"]

        async def _check():
            live = await ms.mcp.get_tool("tortoise_query")
            marker = ((live.meta or {}).get("tortoise") or {}).get("retired")
            assert marker is None, "a live tool was served as retired"

            transform = ms._RetiredToolTransform({s1.name: "sentinel-shim"})

            async def _none(name, *, version=None):
                return None

            assert await transform.get_tool(s1.name, _none) == "sentinel-shim"
            assert await transform.get_tool("tortoise_query", _none) is None

            kept = [type("T", (), {"name": n}) for n in ("tortoise_query", s1.name)]
            filtered = {t.name for t in await transform.list_tools(kept)}
            assert filtered == {"tortoise_query"}

        asyncio.run(_check())


class TestScopeGate:
    """#4170 x #3883: a retired name still resolves through the scope gate.

    A retired name is still SERVED (through the warning shim), so the gate must
    be able to read its permission. While the lookup covered live tools only, a
    scoped caller reached the gate and was told "Unknown tool - denied" - the
    silent disappearance #3883 exists to prevent, for the keys least able to
    debug it.
    """

    def test_every_retired_name_resolves_for_the_gate(self):
        from tortoise.tool_registry import get_tool_by_name

        by_name = get_tool_by_name()
        missing = {s.name for s in RETIRED_TOOL_REGISTRY} - set(by_name)
        assert not missing, f"retired names unresolvable by the gate: {missing}"

    def test_retired_reader_passes_a_read_scope(self):
        spec = next(s for s in RETIRED_TOOL_REGISTRY if not s.writes)
        token = ms._current_scopes.set(["graphs:read"])
        try:
            ms._enforce_mcp_tool_scope(spec.name)  # must not raise
        finally:
            ms._current_scopes.reset(token)

    def test_retired_name_without_a_data_scope_is_gated_not_unknown(self):
        spec = next(s for s in RETIRED_TOOL_REGISTRY if not s.writes)
        token = ms._current_scopes.set([])
        try:
            with pytest.raises(ms.AuthorizationError) as excinfo:
                ms._enforce_mcp_tool_scope(spec.name)
        finally:
            ms._current_scopes.reset(token)
        assert "Unknown tool" not in str(excinfo.value), (
            "a retired name must be gated by its own permission, not denied as unknown")
