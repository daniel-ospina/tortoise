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
        """HTTP_ALLOWED is derived from the registry with correct size + exclusions.

        (Falsifiable after the literal was replaced by the derived set in #454 —
        the old literal-vs-derived comparison became tautological.)
        """
        from tortoise.tool_registry import TOOL_REGISTRY
        from tortoise.mcp_auth import HTTP_ALLOWED

        derived = frozenset(t.name for t in TOOL_REGISTRY if t.http_policy)
        # Derived == literal (no manual sync — #454), and the documented
        # exclusions hold: team_create, backfill_v25, ingest_corpus,
        # index_sessions (privilege/schema/path-traversal) and tortoise_dream
        # (#329 whole-graph EP is CPU-heavy — tenant HTTP excluded).
        assert derived == HTTP_ALLOWED, (
            f"Derived HTTP_ALLOWED mismatch:\n"
            f"  In derived but not set: {derived - HTTP_ALLOWED}\n"
            f"  In set but not derived: {HTTP_ALLOWED - derived}"
        )
        for excluded in ("tortoise_team_create", "tortoise_backfill_v25",
                         "tortoise_ingest_corpus", "tortoise_index_sessions",
                         "tortoise_dream"):
            assert excluded not in HTTP_ALLOWED, f"{excluded} must be HTTP-excluded"

    def test_registry_count(self):
        """91 tools — 60 existing + 6 onboarding (#498/#499/#500) + 1
        human-approval (#531) + 1 #540 + 2 #432 (events_poll, retract_point)
        + 1 #913 (review_connections) + 8 W1–W4 consolidations (#907/#918
        recall, #922 update/delete/operator_action/create_edge, #927
        overview/get, #932 ingest) + 1 epic #900 T7 (#1043, tortoise_index_files)
        + 2 epic #902 A13 (#1051, tortoise_list_batch + tortoise_list_batches)
        + 1 #405 (tortoise_validate_domain) + 1 #438 (find_cross_lens_candidates)
        + 1 #348 (tortoise_audit) + 1 #318 (tortoise_packs_list)
        + 1 #1249 (tortoise_dream_health_check) + 1 #1353
        (tortoise_expand_relationships)."""
        from tortoise.tool_registry import TOOL_REGISTRY
        assert len(TOOL_REGISTRY) == 94, f"Expected 94, got {len(TOOL_REGISTRY)}"
        names = {t.name for t in TOOL_REGISTRY}
        assert "tortoise_validate_domain" in names, "Missing #405 validate_domain tool"
        assert "tortoise_packs_list" in names, "Missing #318 packs_list tool"
        onboarding = {"tortoise_onboarding_demo_create", "tortoise_onboarding_state",
                      "tortoise_onboarding_session_recording",
                      "tortoise_onboarding_github_connect",
                      "tortoise_onboarding_github_status",
                      "tortoise_onboarding_github_index"}
        assert onboarding <= names, f"Missing onboarding tools: {onboarding - names}"
        assert "tortoise_file_human_approval" in names, "Missing #531 human-approval tool"
        assert "tortoise_review_connections" in names, "Missing #913 review_connections tool"
        assert "tortoise_events_poll" in names, "Missing #432 events_poll tool"
        assert "tortoise_retract_point" in names, "Missing #432 retract_point tool"
        assert "tortoise_find_cross_lens_candidates" in names, "Missing #438 cross-lens tool"
        assert "tortoise_audit" in names, "Missing #348 tortoise_audit tool"
        # W1–W4 consolidated tools (#888): recall (W1), update/delete/
        # operator_action/create_edge (W2), overview/get (W3), ingest (W4)
        w_consolidations = {"tortoise_recall", "tortoise_update", "tortoise_delete",
                            "tortoise_operator_action", "tortoise_create_edge",
                            "tortoise_overview", "tortoise_get", "tortoise_ingest"}
        assert w_consolidations <= names, (
            f"Missing W1–W4 tools: {w_consolidations - names}")
        # #454-era surface tools covered by this PR's tests
        for name in ("tortoise_list_tags", "tortoise_suggest_entry_points",
                     "tortoise_get_events"):
            assert name in names, f"Missing tool: {name}"
        # Phase-4 mining/promotion/dedup/timeline surface (#787)
        phase4 = {"tortoise_mine_conversations", "tortoise_list_dedup_candidates",
                  "tortoise_approve_merge", "tortoise_promote_point",
                  "tortoise_belief_timeline"}
        assert phase4 <= names, f"Missing #787 tools: {phase4 - names}"

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
                     "tortoise_ingest_corpus", "tortoise_index_sessions",
                     "tortoise_index_files"}
        for name in excluded:
            assert name in by_name, f"Missing tool: {name}"
            assert by_name[name].http_policy is False, f"{name} should be excluded"


class TestCurationGroups:
    """Epic #888 no-regret: GROUP_BY_NAME coherence fixes (#888 item 3).

    retract_point and events_poll previously fell through to the implicit
    "memory" default via GROUP_BY_NAME.get(t.name, "memory") — events_poll is
    a CDC/subscription tool that belongs in "sessions", and retract_point
    belongs explicitly with the lifecycle tools in "memory" (#432).
    """

    def test_retract_point_explicitly_in_memory(self):
        from tortoise.tool_registry import TOOL_REGISTRY
        entry = next(t for t in TOOL_REGISTRY if t.name == "tortoise_retract_point")
        assert entry.group == "memory", f"got {entry.group}"

    def test_events_poll_in_sessions_group(self):
        from tortoise.tool_registry import TOOL_REGISTRY
        entry = next(t for t in TOOL_REGISTRY if t.name == "tortoise_events_poll")
        assert entry.group == "sessions", f"got {entry.group}"

    def test_groups_reachable_via_helpers(self):
        """tools_by_group / tool_groups expose the corrected groups (#523)."""
        from tortoise.tool_registry import tools_by_group, tool_groups
        memory = {t.name for t in tools_by_group("memory")}
        assert "tortoise_retract_point" in memory
        groups = tool_groups()
        assert "tortoise_events_poll" in groups["sessions"]


class TestDescriptionImprovements:
    """Epic #888 no-regret item 4: sharpened descriptions for the top-confused
    tools (query family, search, entity_profile vs list_topics) so agents can
    pick the right tool from the tools/list descriptions alone.
    """

    @staticmethod
    def _desc(name: str) -> str:
        from tortoise.tool_registry import TOOL_REGISTRY
        return next(t for t in TOOL_REGISTRY if t.name == name).description

    def test_query_description_covers_merged_params(self):
        d = self._desc("tortoise_query")
        assert "offset" in d and "limit" in d and "tag" in d, d
        assert "tortoise_search" in d, "must point at the semantic alternative"

    def test_search_description_distinguishes_from_query(self):
        d = self._desc("tortoise_search")
        assert "semantic" in d.lower(), d
        assert "tortoise_query" in d, "must point at the structural alternative"

    def test_entity_profile_distinguished_from_list_topics(self):
        ep = self._desc("tortoise_entity_profile")
        lt = self._desc("tortoise_list_topics")
        assert "BFS" in ep and "tortoise_list_topics" in ep, ep
        assert "neighbor" in lt.lower() and "tortoise_entity_profile" in lt, lt

    def test_query_aliases_marked_deprecated(self):
        pq = self._desc("tortoise_paginated_query")
        qt = self._desc("tortoise_query_points_by_tag")
        assert "DEPRECATED" in pq and "tortoise_query" in pq, pq
        assert "DEPRECATED" in qt and "tortoise_query" in qt, qt


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
