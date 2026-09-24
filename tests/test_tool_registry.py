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
        """HTTP_ALLOWED stays derived from the registry (#454).

        This is the explicit "HTTP_ALLOWED is registry-derived" pin — it only
        catches a future regression to a hand-maintained literal. The
        *operator-only exclusion* property lives in TestCapabilityModel
        (#4113), keyed on the operation, not on a tool-name string.
        """
        from tortoise.tool_registry import TOOL_REGISTRY  # noqa: I001
        from tortoise.mcp_auth import HTTP_ALLOWED

        derived = frozenset(t.name for t in TOOL_REGISTRY if t.http_policy)
        assert derived == HTTP_ALLOWED, (
            f"Derived HTTP_ALLOWED mismatch:\n"
            f"  In derived but not set: {derived - HTTP_ALLOWED}\n"
            f"  In set but not derived: {HTTP_ALLOWED - derived}"
        )

    def test_registry_count(self):
        """82 live tools = 98 − the 16 owner-approved retirements (#3863).
        The census is bumped per add."""
        from tortoise.tool_registry import RETIRED_TOOL_REGISTRY, TOOL_REGISTRY
        assert len(TOOL_REGISTRY) == 82, f"Expected 82, got {len(TOOL_REGISTRY)}"
        names = {t.name for t in TOOL_REGISTRY}
        # The 16 retired names are NOT live, and each one is declared retired.
        retired = {t.name for t in RETIRED_TOOL_REGISTRY}
        assert len(retired) == 16, f"Expected 16 retired, got {len(retired)}"
        assert not (retired & names), f"Retired names still live: {retired & names}"
        assert "tortoise_validate_domain" in names, "Missing #405 validate_domain tool"
        assert "tortoise_packs_list" in names, "Missing #318 packs_list tool"
        assert "tortoise_graph_set_recording" in names, "Missing #2302 graph_set_recording tool"
        onboarding = {"tortoise_onboarding_demo_create", "tortoise_onboarding_state",
                      "tortoise_onboarding_session_recording",
                      "tortoise_onboarding_github_connect",
                      "tortoise_onboarding_github_status",
                      "tortoise_onboarding_github_index",
                      "tortoise_onboarding_seed"}
        assert onboarding <= names, f"Missing onboarding tools: {onboarding - names}"
        assert "tortoise_file_human_approval" in names, "Missing #531 human-approval tool"
        assert "tortoise_review_connections" in names, "Missing #913 review_connections tool"
        assert "tortoise_events_poll" in names, "Missing #432 events_poll tool"
        assert "tortoise_retract_point" in names, "Missing #432 retract_point tool"
        assert "tortoise_find_cross_lens_candidates" in names, "Missing #438 cross-lens tool"
        assert "tortoise_audit" in names, "Missing #348 tortoise_audit tool"
        # W1–W4 consolidated tools (#888): recall (W1), update/delete/
        # operator_action/create_edge (W2), overview/get_entity (W3), ingest (W4).
        # W3's fetch tool is `tortoise_get_entity` — `tortoise_get` retires into it
        # (owner decision, docs/product/canonical-mcp-tools.md @ approval_pr 4120).
        w_consolidations = {"tortoise_recall", "tortoise_update", "tortoise_delete",
                            "tortoise_operator_action", "tortoise_create_edge",
                            "tortoise_overview", "tortoise_get_entity", "tortoise_ingest"}
        assert w_consolidations <= names, (
            f"Missing W1–W4 tools: {w_consolidations - names}")
        # #454-era surface tools covered by this PR's tests
        for name in ("tortoise_query", "tortoise_suggest_entry_points",
                     "tortoise_overview"):
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
        """Operator-only/FS operation bindings are http_policy=False (#4113).

        Keyed on the operation (sdk_method), not a tool-name string — a rename
        or merge keeps the binding resolvable.
        """
        from tool_surface_capabilities import (  # noqa: I001
            HTTP_EXCLUDED_SDK_METHODS, registry_entries_by_method,
            sdk_filesystem_methods, sdk_operator_only_mutators,
        )

        by_method = registry_entries_by_method()
        operator_only = (sdk_filesystem_methods() | sdk_operator_only_mutators()
                         | set(HTTP_EXCLUDED_SDK_METHODS))
        # Non-vacuity sentinel: the derived set really matched something.
        assert {"ingest_corpus", "index_directory", "org_create"} <= operator_only
        for method in sorted(operator_only):
            for entry in by_method.get(method, []):
                assert entry.http_policy is False, (
                    f"{entry.name} binds operator-only/filesystem operation "
                    f"{method!r} but is HTTP-exposed")


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

    def test_session_capture_tool_grouped_sessions(self):
        """#1727 (Task 13): tortoise_session_capture groups under "sessions"
        (else it falls to the implicit "memory" default and is filtered out
        of sessions-group surfaces)."""
        from tortoise.tool_registry import TOOL_REGISTRY, tools_by_group
        entry = next(t for t in TOOL_REGISTRY
                     if t.name == "tortoise_session_capture")
        assert entry.group == "sessions", f"got {entry.group}"
        assert entry.http_policy is True, "must be exposed on HTTP surfaces"
        assert any(t.name == "tortoise_session_capture"
                   for t in tools_by_group("sessions"))

    def test_graph_set_recording_tool_grouped_sessions(self):
        """#2302: tortoise_graph_set_recording groups under "sessions" with
        capture + graph listing (per-graph recording is session-capture
        management state) — pinned so a future edit can't silently drop it
        to the "memory" default and filter it off sessions-group surfaces."""
        from tortoise.tool_registry import TOOL_REGISTRY, tools_by_group
        entry = next(t for t in TOOL_REGISTRY
                     if t.name == "tortoise_graph_set_recording")
        assert entry.group == "sessions", f"got {entry.group}"
        assert entry.http_policy is True, "must be exposed on HTTP surfaces"
        assert entry.annotations is not None \
            and entry.annotations.readOnlyHint is False, \
            "override writes are mutations (write-classified)"
        assert any(t.name == "tortoise_graph_set_recording"
                   for t in tools_by_group("sessions"))

    def test_groups_reachable_via_helpers(self):
        """tools_by_group / tool_groups expose the corrected groups (#523)."""
        from tortoise.tool_registry import tools_by_group, tool_groups  # noqa: I001
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

    def test_query_aliases_retired(self):
        """The two query aliases are RETIRED (#3883), keeping their DEPRECATED
        description and naming tortoise_query as the replacement."""
        from tortoise.tool_registry import RETIRED_TOOL_REGISTRY
        by_name = {t.name: t for t in RETIRED_TOOL_REGISTRY}
        pq = by_name["tortoise_paginated_query"]
        qt = by_name["tortoise_query_points_by_tag"]
        assert "DEPRECATED" in pq.description and "tortoise_query" in pq.description, pq.description
        assert "DEPRECATED" in qt.description and "tortoise_query" in qt.description, qt.description
        assert pq.retired_use_instead.startswith("tortoise_query")
        assert qt.retired_use_instead.startswith("tortoise_query")


class TestFastMCPAdapter:
    """Gate 2: MCP adapter emits correct tools from registry."""

    def test_adapter_registers_all_tools(self):
        """Every registry entry becomes a registered MCP tool."""
        async def _check():
            from tortoise.tool_registry import TOOL_REGISTRY, FastMCPAdapter  # noqa: I001
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
            from tortoise.tool_registry import TOOL_REGISTRY, FastMCPAdapter  # noqa: I001
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
            from tortoise.tool_registry import TOOL_REGISTRY, FastMCPAdapter  # noqa: I001
            from fastmcp import FastMCP
            from tool_surface_capabilities import (
                HTTP_EXCLUDED_SDK_METHODS, registry_entries_by_method,
                sdk_filesystem_methods, sdk_operator_only_mutators,
            )

            # Derive the excluded names BY CAPABILITY — no literal tool name.
            by_method = registry_entries_by_method()
            operator_only = (sdk_filesystem_methods() | sdk_operator_only_mutators()
                             | set(HTTP_EXCLUDED_SDK_METHODS))
            excluded = {e.name for m in operator_only for e in by_method.get(m, [])}
            # `by_method` covers the SERVED set (#3883), but the adapter registers the
            # LIVE registry — a retired name is served through the warning shim instead,
            # which the two assertions below pin. So compare against the live half.
            excluded &= {e.name for e in TOOL_REGISTRY}
            assert "tortoise_org_create" in excluded  # non-vacuity sentinel

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
            missing = excluded - registered
            assert not missing, f"excluded tools not registered: {missing}"
            # The two legacy bulk writers are RETIRED (#3883): off the advertised
            assert "tortoise_ingest_corpus" not in registered
            assert "tortoise_index_sessions" not in registered

        asyncio.run(_check())


class TestFastAPIRouterAdapter:
    """Gate 3: REST adapter generates correct routes from registry."""

    def test_adapter_registers_all_rest_routes(self):
        """Every registry entry with rest_spec becomes a route."""
        from tortoise.tool_registry import TOOL_REGISTRY, FastAPIRouterAdapter  # noqa: I001
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


# ── #4113: capability-not-name guards ───────────────────────────────────────
#
# The guards above/below this class used to assert safety properties with
# hardcoded tool-name strings; a rename or merge in the #3863/#3994 surface
# cutover made them vacuous. These guards key on the OPERATION and carry a
# falsifiability test per declared threat class (T1–T4), so they cannot
# silently pass.

_PROBE_SRC = '''
def _get_org_sdk():
    ...

def _safe(fn, *a, **k):
    ...

def _quota_gated(fn, *a, **k):
    ...

def _http_excluded_error():
    ...

def tortoise_probe_reader():
    return _safe(_quota_gated(_get_org_sdk().create_point, "points"))

def tortoise_probe_alias():
    sdk = _get_org_sdk()
    return _safe(_quota_gated(sdk.create_point, "points"))

def tortoise_probe_fs():
    return _safe(_get_org_sdk().ingest_corpus, "/tmp")

def tortoise_probe_org():
    return _safe(_get_org_sdk().org_create, "team")

def tortoise_probe_guarded_excluded():
    if _transport_is_http():
        return _http_excluded_error()
    return _safe(_quota_gated(_get_org_sdk().create_point, "points"))

def tortoise_probe_unguarded_excluded():
    return _safe(_quota_gated(_get_org_sdk().create_point, "points"))

def tortoise_probe_dynamic_table():
    handlers = {}
    return handlers["create_point"]()

def tortoise_probe_wrap_dynamic():
    return _safe(_quota_gated(dispatch["x"], "points"))

def tortoise_probe_resolve_then_call():
    h = _TABLE["create_point"]
    return h()

def tortoise_probe_getattr_then_call():
    fn = getattr(_get_org_sdk(), "create_point")
    return fn()

def tortoise_probe_conditional_guard(n):
    if n == 0:
        return _http_excluded_error()
    return _safe(_quota_gated(_get_org_sdk().create_point, "points"))

def tortoise_probe_dead_guard():
    if False:
        return _http_excluded_error()
    return _safe(_quota_gated(_get_org_sdk().create_point, "points"))

def _transport_is_http():
    ...

def _require_stdio():
    return _http_excluded_error()

def tortoise_probe_helper_guard():
    if _transport_is_http():
        return _require_stdio()
    return _safe(_get_org_sdk().ingest_corpus, "/tmp")

def tortoise_probe_get_dispatch():
    h = _HANDLERS.get("create_point")
    return h()

def _probe_helper():
    return _get_org_sdk().create_point

_probe_alias = _probe_helper

def tortoise_probe_alias_helper():
    return _probe_alias()

def _passed_handle_do(client):
    return client.create_point

def tortoise_probe_passed_handle():
    return _passed_handle_do(_get_org_sdk())

def tortoise_probe_inverted_guard():
    if not _transport_is_http():
        return _http_excluded_error()
    return _safe(_get_org_sdk().create_point, "x")
'''

_BARE_IMPORT_SRC = '''
from tortoise.sdk import create_point
import tortoise.sdk as sdk

def _get_org_sdk():
    ...

def tortoise_probe_bare_import():
    return create_point("x")

def tortoise_probe_module_alias():
    return sdk.create_point("x")
'''

_SDK_HELPER_DELEGATION_SRC = '''
def _mod_mut():
    conn.query("CREATE (n:X)")

def _mod_mut_2():
    return _mod_mut()

class TortoiseSDK:
    def via_mod_mut(self):
        return _mod_mut()

    def chain(self):
        return _mod_mut_2()
'''

# A synthetic ledgered entry.  The ledger tests monkeypatch
# DECLARED_BINDING_DIVERGENCES with this tool instead of using the REAL ledger
# keys, because the real ledger is emptied as its two entries are repaired — and a
# test bound to a live ledger would red the build on exactly the action the guard
# itself instructs ("delete the ledger entry").
_LEDGER_TOOL = "tortoise_probe_ledgered"
_LEDGER_DECLARED = "query"
_LEDGER_OTHER = "recall_state"


def _ledger_src(reaches: str) -> str:
    """Source whose handler for `_LEDGER_TOOL` reaches `reaches`."""
    return (
        "def _get_org_sdk():\n"
        "    ...\n\n"
        f"def {_LEDGER_TOOL}(id):\n"
        f"    return _get_org_sdk().{reaches}(id)\n"
    )


def _probe(name: str, sdk_method: str, *, annotations, http_policy: bool, writes: bool = False):
    from tortoise.tool_registry import ToolDefinition
    return ToolDefinition(
        name=name, id=f"surface.probe.{name.removeprefix('tortoise_')}",
        description="probe", annotations=annotations,
        http_policy=http_policy, sdk_method=sdk_method, writes=writes,
    )


class TestCapabilityModel:
    """#4113 — guards derived from operation capability, not tool names."""

    def test_derived_sets_are_non_vacuous(self):
        """Sentinels: the derivations really matched (and did not over-match)."""
        from tool_surface_capabilities import (  # noqa: I001
            sdk_filesystem_methods, sdk_graph_mutators, sdk_operator_only_mutators,
        )
        mutators = sdk_graph_mutators()
        assert {"create_point", "delete", "update"} <= mutators
        assert not ({"query", "get_point", "list_pointkinds"} & mutators)
        # over-inclusion sentinel: control-plane READS are not mutators
        assert not ({"org_list", "graph_list", "apikey_list", "org_get"} & mutators)
        fs = sdk_filesystem_methods()
        assert {"ingest_corpus", "index_directory"} <= fs
        assert not ({"_probe_embedded_busy", "session_index_health"} & fs)
        op = sdk_operator_only_mutators()
        assert {"org_create", "membership_create", "apikey_revoke",
                "invitation_create"} <= op
        assert not ({"org_list", "graph_list", "apikey_list"} & op)

    def test_handler_operations_reads_value_passed_and_alias_forms(self):
        """`_get_org_sdk().<m>` as a passed value and via an alias are both seen."""
        from tool_surface_capabilities import handler_operations
        assert "create_point" in handler_operations("tortoise_create_point").operations
        assert "compute_confidence" in handler_operations("tortoise_compute_confidence").operations

    def test_operator_only_and_filesystem_capabilities_are_http_excluded(self):
        from tool_surface_capabilities import (
            privileged_exposure_violations,
            served_registry,
        )

        assert privileged_exposure_violations(served_registry()) == []

    def test_write_capability_is_classified(self):
        from tool_surface_capabilities import (
            served_registry,
            write_classification_violations,
        )

        assert write_classification_violations(served_registry()) == []

    def test_bindings_resolve(self):
        from tool_surface_capabilities import (
            binding_resolution_violations,
            served_registry,
        )

        assert binding_resolution_violations(served_registry()) == []

    def test_declared_sets_are_live(self):
        """Every declared set entry resolves — a rename fails loudly, it does
        not silently drop out of its check."""
        from tool_surface_capabilities import declared_set_violations, served_registry

        assert declared_set_violations(served_registry()) == []

    def test_guards_cover_the_served_set_not_only_the_live_one(self):
        """#3883: a retired name is still resolvable and callable by name, so the
        capability guards must see it. A live-only default left a retired entry's
        handler and binding outside every guard — the exact hole that let a
        retired writer's `writes` flag go unchecked."""
        from tool_surface_capabilities import (
            all_handler_operations,
            registry_entries_by_method,
            served_registry,
        )

        from tortoise.tool_registry import RETIRED_TOOL_REGISTRY, TOOL_REGISTRY
        retired = {e.name for e in RETIRED_TOOL_REGISTRY}
        live = {e.name for e in TOOL_REGISTRY}
        assert retired, "the retirement set is empty — this test would be vacuous"
        served = {e.name for e in served_registry()}
        assert served == live | retired

        # The guard defaults must cover the retired half, not just the live half.
        assert retired <= set(all_handler_operations())
        by_method = registry_entries_by_method()
        bound = {e.name for entries in by_method.values() for e in entries}
        assert retired & bound, (
            "no retired entry is reachable through the binding index — the guards "
            "would not see a retired entry's declared binding")

    # ── falsifiability — each declared threat class must be able to fail ──

    def test_T1_inverted_self_guard_fails(self):
        """A guard whose transport test is NEGATED lets HTTP through — it must
        not count as a self-guard."""
        from tool_surface_capabilities import (
            handler_self_guards,
            write_classification_violations,
        )

        from tortoise.tool_registry import _rw
        entry = _probe("tortoise_probe_inverted_guard", "create_point",
                       annotations=_rw(), http_policy=False)
        assert not handler_self_guards("tortoise_probe_inverted_guard", _PROBE_SRC)
        assert write_classification_violations([entry], _PROBE_SRC)

    def test_T1_bare_import_and_module_alias_fail(self):
        """`from tortoise.sdk import create_point` and `import tortoise.sdk as s;
        s.create_point` are both statically resolvable writes."""
        from tool_surface_capabilities import write_classification_violations

        from tortoise.tool_registry import _ro
        for probe in ("tortoise_probe_bare_import", "tortoise_probe_module_alias"):
            entry = _probe(probe, "query", annotations=_ro(), http_policy=True)
            assert write_classification_violations([entry], _BARE_IMPORT_SRC), probe

    def test_T4_per_site_duplicate_weight_fails(self):
        """T4: a per-METHOD model would collapse this; per-site must catch one
        weighted and one unweighted site for the same method."""
        from tool_surface_capabilities import wrap_site_violations
        src = (
            "def _get_org_sdk(): ...\n"
            "def _safe(fn, *a, **k): ...\n"
            "def _quota_gated(fn, *a, **k): ...\n"
            "def a(): return _quota_gated(_get_org_sdk().create_point, abuse_weight=1)\n"
            "def b(): return _quota_gated(_get_org_sdk().create_point)\n"
        )
        assert wrap_site_violations(src)

    def test_T1_read_labelled_tool_reaching_a_write_fails(self):
        """T1: a merged tool labelled read-only whose handler writes."""
        from tool_surface_capabilities import write_classification_violations

        from tortoise.tool_registry import _ro
        for probe in ("tortoise_probe_reader", "tortoise_probe_alias"):
            entry = _probe(probe, "query", annotations=_ro(), http_policy=True)
            assert write_classification_violations([entry], _PROBE_SRC), probe

    def test_T1_exempted_tool_without_self_guard_fails(self):
        """T1 variant: HTTP-excluded is NOT enough — the handler must self-guard."""
        from tool_surface_capabilities import write_classification_violations

        from tortoise.tool_registry import _rw
        unguarded = _probe("tortoise_probe_unguarded_excluded", "create_point",
                           annotations=_rw(), http_policy=False)
        guarded = _probe("tortoise_probe_guarded_excluded", "create_point",
                         annotations=_rw(), http_policy=False)
        assert write_classification_violations([unguarded], _PROBE_SRC)
        assert not write_classification_violations([guarded], _PROBE_SRC)

    def test_T2_http_tool_reaching_privileged_capability_fails(self):
        """T2: filesystem and control-plane legs."""
        from tool_surface_capabilities import privileged_exposure_violations

        from tortoise.tool_registry import _ro
        for probe in ("tortoise_probe_fs", "tortoise_probe_org"):
            entry = _probe(probe, "query", annotations=_ro(), http_policy=True)
            assert privileged_exposure_violations([entry], _PROBE_SRC), probe

    def test_T3_stale_binding_and_missing_handler_fail(self):
        """T3: a renamed/removed operation or handler must fail, not vanish."""
        from tool_surface_capabilities import binding_resolution_violations

        from tortoise.tool_registry import _ro
        stale = _probe("tortoise_create_point", "no_such_method_xyz",
                       annotations=_ro(), http_policy=True)
        assert any("does not resolve" in v
                   for v in binding_resolution_violations([stale]))
        handlerless = _probe("tortoise_no_handler_at_all", "query",
                             annotations=_ro(), http_policy=True)
        assert any("no module-level handler" in v
                   for v in binding_resolution_violations([handlerless]))
        dynamic = _probe("tortoise_probe_dynamic_table", "query",
                         annotations=_ro(), http_policy=True)
        assert any("dispatch" in v
                   for v in binding_resolution_violations([dynamic], _PROBE_SRC))

    def test_T1_resolve_then_call_dispatch_fails_closed(self):
        """T1: a name-keyed dispatch table bound then called (`h = _TABLE[k]; h()`)
        must fail closed, not silently yield no operations."""
        from tool_surface_capabilities import binding_resolution_violations

        from tortoise.tool_registry import _ro
        for probe in ("tortoise_probe_resolve_then_call",
                      "tortoise_probe_getattr_then_call"):
            entry = _probe(probe, "query", annotations=_ro(), http_policy=True)
            violations = binding_resolution_violations([entry], _PROBE_SRC)
            assert any("dispatch" in v for v in violations), (probe, violations)

    def test_T3_divergence_ledger_checked_when_declaration_does_not_resolve(self, monkeypatch):
        """A ledgered entry whose declaration drifts to an already-exempt dangling
        name (a `DANGLING_SDK_DECLARATIONS` member, which does not resolve) must
        still be reported.  Skipping non-resolving declarations — correct for
        unledgered entries — would leave a stale ledger entry green forever."""
        import tool_surface_capabilities as tsc

        from tortoise.tool_registry import _ro
        monkeypatch.setattr(tsc, "DECLARED_BINDING_DIVERGENCES", {
            _LEDGER_TOOL: tsc.DeclaredBindingDivergence(
                _LEDGER_DECLARED, frozenset({_LEDGER_OTHER}), "synthetic"),
        })
        drifted = _probe(_LEDGER_TOOL, "upsert_tenant_manifest",
                         annotations=_ro(), http_policy=True)
        violations = tsc.binding_resolution_violations(
            [drifted], _ledger_src(_LEDGER_OTHER))
        assert any("update the entry" in v for v in violations), violations

    def test_T3_handlerless_entry_is_not_a_binding_divergence(self):
        """An entry with no module-level handler is already reported by the
        handler-existence arm.  Asserting it *also* \"never reaches\" its
        declaration is noise — it names a handler that does not exist."""
        from tool_surface_capabilities import binding_resolution_violations

        from tortoise.tool_registry import _ro
        handlerless = _probe("tortoise_no_handler_at_all", "query",
                             annotations=_ro(), http_policy=True)
        violations = binding_resolution_violations([handlerless])
        assert any("no module-level handler" in v for v in violations), violations
        assert not any("never" in v for v in violations), violations

    def test_T3_declared_binding_must_be_reached_not_merely_resolvable(self):
        """#4337: a declared `sdk_method` that RESOLVES but is never called must
        fail. `query` is a real method on TortoiseSDK, so the resolution arm
        passes it; only reading the handler body can see that this handler calls
        `create_point` instead."""
        from tool_surface_capabilities import binding_resolution_violations

        from tortoise.tool_registry import _ro
        diverged = _probe("tortoise_probe_reader", "query",
                          annotations=_ro(), http_policy=True)
        violations = binding_resolution_violations([diverged], _PROBE_SRC)
        assert any("never" in v and "query" in v for v in violations), violations

    def test_T3_divergence_ledger_cannot_outlive_its_defect(self, monkeypatch):
        """The ledger is exact in BOTH directions: a repaired divergence must red
        the build until its entry is deleted.  Run on a SYNTHETIC ledger so the
        test survives the repair of the real entries."""
        import tool_surface_capabilities as tsc

        from tortoise.tool_registry import _ro
        monkeypatch.setattr(tsc, "DECLARED_BINDING_DIVERGENCES", {
            _LEDGER_TOOL: tsc.DeclaredBindingDivergence(
                _LEDGER_DECLARED, frozenset({_LEDGER_OTHER}), "synthetic"),
        })
        repaired = _probe(_LEDGER_TOOL, _LEDGER_DECLARED,
                          annotations=_ro(), http_policy=True)
        violations = tsc.binding_resolution_violations(
            [repaired], _ledger_src(_LEDGER_DECLARED))
        assert any("delete the ledger entry" in v for v in violations), violations

    def test_T3_divergence_ledger_records_the_declared_method(self, monkeypatch):
        """A ledger keyed on the tool name alone is a blanket exemption: the entry
        could re-diverge to a DIFFERENT declared method and stay green.  This
        covers the declared half of the recorded divergence."""
        import tool_surface_capabilities as tsc

        from tortoise.tool_registry import _ro
        monkeypatch.setattr(tsc, "DECLARED_BINDING_DIVERGENCES", {
            _LEDGER_TOOL: tsc.DeclaredBindingDivergence(
                _LEDGER_DECLARED, frozenset({_LEDGER_OTHER}), "synthetic"),
        })
        drifted = _probe(_LEDGER_TOOL, "get_point",
                         annotations=_ro(), http_policy=True)
        violations = tsc.binding_resolution_violations(
            [drifted], _ledger_src(_LEDGER_OTHER))
        assert any("update the entry" in v for v in violations), violations

    def test_T3_declared_private_method_counts_as_reached(self, monkeypatch):
        """Presence of the declared method is asked of the RAW reach set.  A
        declared PRIVATE SDK method is a real binding: filtering it out first would
        report it unreached, and a ledgered one could never clear its entry."""
        import tool_surface_capabilities as tsc

        from tortoise.tool_registry import _ro
        private = _probe(_LEDGER_TOOL, "_get_proj",
                         annotations=_ro(), http_policy=True)
        monkeypatch.setattr(tsc, "DECLARED_BINDING_DIVERGENCES", {})
        assert not any("never" in v for v in tsc.binding_resolution_violations(
            [private], _ledger_src("_get_proj")))

    def test_T3_divergence_ledger_records_the_reached_set(self, monkeypatch):
        """The REACHED half of the recorded divergence, on its own.  Without this
        the reached comparison is dead weight — the declared-drift test exercises
        only the other conjunct, so deleting the reached check leaves the suite
        green."""
        import tool_surface_capabilities as tsc

        from tortoise.tool_registry import _ro
        monkeypatch.setattr(tsc, "DECLARED_BINDING_DIVERGENCES", {
            _LEDGER_TOOL: tsc.DeclaredBindingDivergence(
                _LEDGER_DECLARED, frozenset(), "synthetic"),
        })
        same_declared = _probe(_LEDGER_TOOL, _LEDGER_DECLARED,
                               annotations=_ro(), http_policy=True)
        violations = tsc.binding_resolution_violations(
            [same_declared], _ledger_src(_LEDGER_OTHER))
        assert any("update the entry" in v for v in violations), violations

    def test_T1_partial_or_dead_self_guard_fails(self):
        """T1: presence of `_http_excluded_error` is not enough — a conditional
        or dead guard does not dominate the write, so it must fail."""
        from tool_surface_capabilities import (
            handler_self_guards,
            write_classification_violations,
        )

        from tortoise.tool_registry import _rw
        for probe in ("tortoise_probe_conditional_guard",
                      "tortoise_probe_dead_guard"):
            entry = _probe(probe, "create_point", annotations=_rw(), http_policy=False)
            assert not handler_self_guards(probe, _PROBE_SRC), probe
            assert write_classification_violations([entry], _PROBE_SRC), probe
        # a helper-delegated guard DOES count (no false-red)
        assert handler_self_guards("tortoise_probe_helper_guard", _PROBE_SRC)

    def test_exemption_set_is_exact(self):
        """2b: the non-HTTP writer exemption set is exactly NON_HTTP_WRITER_TOOLS —
        measured over the SERVED set, retired names included (#3883): a retired
        writer is still callable by name, so it is not exempt from the guard."""
        from tool_surface_capabilities import exemption_set_violations, served_registry

        from tortoise.tool_registry import _rw
        assert exemption_set_violations(served_registry()) == []
        rogue = _probe("tortoise_rogue_writer", "query", annotations=_rw(),
                       http_policy=False)
        assert exemption_set_violations([rogue])

    def test_guard4_rename_simulation_fails_loudly(self):
        """A stale write-surface map (renamed/removed tool, or a declared write
        whose wrap site vanished) must fail — never silently pass."""
        from tool_surface_capabilities import (
            DECLARED_WRITE_SURFACE_MAP,
            write_surface_map_violations,
        )
        # a renamed tool name is no longer live → dead-name violation
        renamed = dict(DECLARED_WRITE_SURFACE_MAP)
        renamed["create_point"] = "tortoise_create_point_RENAMED"
        assert any("non-existent" in v
                   for v in write_surface_map_violations(renamed))
        # a declared method whose wrap site is gone → inverse violation
        dropped = dict(DECLARED_WRITE_SURFACE_MAP)
        dropped["no_such_wrapped_method"] = "tortoise_create_point"
        assert any("stale map" in v
                   for v in write_surface_map_violations(dropped))
        # FORWARD: a wrap site with no map entry is reported (no KeyError)
        omitted = {k: v for k, v in DECLARED_WRITE_SURFACE_MAP.items()
                   if k != "update_point"}
        assert any("unmapped" in v
                   for v in write_surface_map_violations(omitted))

    def test_T1_dict_get_dispatch_fails_closed(self):
        """T1: `h = _HANDLERS.get(k); h()` (the idiomatic dispatch table) must
        fail closed, not silently yield no operations."""
        from tool_surface_capabilities import binding_resolution_violations

        from tortoise.tool_registry import _ro
        entry = _probe("tortoise_probe_get_dispatch", "query",
                       annotations=_ro(), http_policy=True)
        violations = binding_resolution_violations([entry], _PROBE_SRC)
        assert any("dispatch" in v for v in violations), violations

    def test_T1_alias_helper_and_passed_handle_fail(self):
        """T1: a module-level function alias and an SDK handle passed to a
        helper are both statically resolvable and must be caught."""
        from tool_surface_capabilities import (
            handler_operations,
            write_classification_violations,
        )

        from tortoise.tool_registry import _ro
        for probe in ("tortoise_probe_alias_helper", "tortoise_probe_passed_handle"):
            assert "create_point" in handler_operations(probe, _PROBE_SRC).operations
            entry = _probe(probe, "query", annotations=_ro(), http_policy=True)
            assert write_classification_violations([entry], _PROBE_SRC), probe

    def test_sdk_mutator_closure_follows_module_helpers(self):
        """sdk_graph_mutators() must propagate through a module-level helper."""
        from tool_surface_capabilities import sdk_graph_mutators
        mutators = sdk_graph_mutators(_SDK_HELPER_DELEGATION_SRC)
        assert {"mod:_mod_mut", "via_mod_mut", "chain"} <= mutators

    def test_leading_benign_statement_does_not_false_red_self_guard(self):
        """A benign assignment before the HTTP guard is still a valid guard."""
        from tool_surface_capabilities import handler_self_guards
        src = (
            "def _transport_is_http(): ...\n"
            "def _http_excluded_error(): ...\n"
            "def _get_org_sdk(): ...\n"
            "def _safe(fn, *a, **k): ...\n"
            "def tortoise_probe_benign():\n"
            "    x = 1\n"
            "    if _transport_is_http():\n"
            "        return _http_excluded_error()\n"
            "    return _safe(_get_org_sdk().create_point, 'x')\n"
        )
        assert handler_self_guards("tortoise_probe_benign", src)

    def test_T4_dynamic_wrap_site_fails(self):
        """T4: a wrap site whose target cannot be resolved statically fails."""
        from tool_surface_capabilities import quota_gated_wrap_sites
        unresolved = quota_gated_wrap_sites(_PROBE_SRC).unresolved
        assert unresolved, "dynamic wrap site not detected"
        assert any("unresolvable" in u for u in unresolved)

    def test_declared_set_violations_can_fail(self):
        """The declared-set liveness predicate is itself falsifiable."""
        from tool_surface_capabilities import declared_set_violations

        from tortoise.tool_registry import TOOL_REGISTRY
        without_dream = [e for e in TOOL_REGISTRY if e.sdk_method != "dream"]
        assert any("no tool binding" in v or "does not resolve" in v
                   for v in declared_set_violations(without_dream))
        # a synthetic SDK lacking a declared method fails resolution
        assert any("does not resolve" in v
                   for v in declared_set_violations(TOOL_REGISTRY,
                                                   sdk_src="class TortoiseSDK:\n    pass\n"))

    def test_read_through_non_sdk_binding_is_checked_by_tool_name(self):
        """#4035 review (P1): the READ_THROUGH read-classification property must
        survive the blanked declaration.  `tortoise_packs_list` no longer declares
        `sdk_method="get_tenant_packs"`, so the `by_method` loop in
        declared_set_violations can never key on it; the property is asserted per
        TOOL NAME via NON_SDK_READ_BINDINGS.  Mutation: flip the tool to a writer
        annotation and the guard must go red.
        """
        import dataclasses

        import tool_surface_capabilities as tsc
        from tool_surface_capabilities import declared_set_violations, served_registry

        from tortoise.tool_registry import _rw

        # The real registry is clean — the violation below is caused by the probe.
        assert declared_set_violations(served_registry()) == []
        # ... and the op is genuinely NOT declared on any entry (the premise of
        # the finding): if it were, the old `by_method` loop would cover it.
        assert not any(e.sdk_method == "get_tenant_packs" for e in served_registry())
        assert tsc.NON_SDK_READ_BINDINGS["get_tenant_packs"] == "tortoise_packs_list"

        flipped = [dataclasses.replace(e, annotations=_rw())
                   if e.name == "tortoise_packs_list" else e
                   for e in served_registry()]
        assert any("get_tenant_packs" in v and "non-read HTTP tool" in v
                   for v in declared_set_violations(flipped)), (
            "the read-classification property is unchecked for a handler-served "
            "read-through op")

    def test_read_through_non_sdk_binding_is_live(self):
        """#4035 review (P1): the op→tool binding is live — a vanished bound tool
        fails loudly instead of silently dropping the check."""
        from tool_surface_capabilities import declared_set_violations, served_registry

        without = [e for e in served_registry() if e.name != "tortoise_packs_list"]
        assert any(
            "NON_SDK_READ_OPERATIONS entry 'get_tenant_packs' has no tool binding" in v
            for v in declared_set_violations(without))

    def test_read_through_non_sdk_op_is_not_a_resolution_exemption(self):
        """#4035 review (P1 consequence 2): `get_tenant_packs` must NOT stay in
        DANGLING_SDK_DECLARATIONS.  Its only declaration was blanked, so keeping
        it there would exempt a *future* row that re-declares it from the
        resolution arm — the re-declaration is the defect, not an exemption.
        Mutation: a synthetic entry declaring it must fail resolution.
        """
        from tool_surface_capabilities import (
            DANGLING_SDK_DECLARATIONS,
            binding_resolution_violations,
        )

        from tortoise.tool_registry import _ro
        assert "get_tenant_packs" not in DANGLING_SDK_DECLARATIONS
        assert "health" in DANGLING_SDK_DECLARATIONS  # the legitimately-declared row

        redeclared = _probe("tortoise_probe_redeclared_packs", "get_tenant_packs",
                            annotations=_ro(), http_policy=True)
        assert any("does not resolve" in v
                   for v in binding_resolution_violations([redeclared]))

    def test_second_nonsdk_writer_declarer_is_checked(self):
        """#4035 review (P2d): the writer-op check keeps its OP-KEYED arm.  The
        name-keyed NON_SDK_WRITER_BINDINGS arm alone checked one named tool; a
        SECOND entry declaring `upsert_tenant_manifest` read-annotated passed both
        that arm and the resolution exemption.  Mutation: it must now go red.
        """
        from tool_surface_capabilities import (
            served_registry,
            write_classification_violations,
        )

        from tortoise.tool_registry import _ro
        second = _probe("tortoise_probe_second_manifest_writer",
                        "upsert_tenant_manifest", annotations=_ro(), http_policy=True)
        assert any("non-SDK writer operation" in v
                   for v in write_classification_violations(
                       [*served_registry(), second]))

    def test_declared_binding_divergence_ledger_is_live(self, monkeypatch):
        """#4337: a ledger key whose registry entry is gone must fail, not persist
        unexamined — the name-goes-stale vacuity #4113 exists to remove. The
        divergence arm iterates ENTRIES, so it cannot see a key with no entry."""
        import tool_surface_capabilities as tsc

        from tortoise.tool_registry import TOOL_REGISTRY
        monkeypatch.setattr(tsc, "DECLARED_BINDING_DIVERGENCES", {
            "tortoise_probe_absent_ledger_key": tsc.DeclaredBindingDivergence(
                _LEDGER_DECLARED, frozenset(), "synthetic"),
        })
        assert any("no registry entry" in v
                   for v in tsc.declared_set_violations(TOOL_REGISTRY))

    def test_guard1_declared_and_unguarded_branches_fail(self):
        """Guard 1 must fire on the declared-binding leg and on an HTTP-excluded
        tool that reaches a privileged op without self-guarding."""
        from tool_surface_capabilities import privileged_exposure_violations

        from tortoise.tool_registry import _ro, _rw
        declared = _probe("tortoise_probe_fs", "ingest_corpus",
                          annotations=_ro(), http_policy=True)
        assert privileged_exposure_violations([declared], _PROBE_SRC)
        unguarded = _probe("tortoise_probe_unguarded_excluded", "ingest_corpus",
                           annotations=_rw(), http_policy=False)
        assert privileged_exposure_violations([unguarded], _PROBE_SRC)

    def test_guard2_undeclared_and_nonsdk_branches_fail(self):
        """Guard 2's 2c (empty sdk_method) and non-SDK-writer branches fail."""
        from tool_surface_capabilities import write_classification_violations

        from tortoise.tool_registry import _ro
        undeclared = _probe("tortoise_undeclared_empty", "", annotations=_ro(),
                            http_policy=True)
        assert any("empty sdk_method" in v
                   for v in write_classification_violations([undeclared], _PROBE_SRC))
        non_sdk = _probe("tortoise_pack_install", "upsert_tenant_manifest",
                         annotations=_ro(), http_policy=True)
        assert any("non-SDK writer" in v
                   for v in write_classification_violations([non_sdk], _PROBE_SRC))

    def test_read_through_write_is_not_flagged(self):
        """The read-through subtraction: a read-only HTTP tool reaching
        `compute_confidence` is deliberately NOT a violation."""
        from tool_surface_capabilities import write_classification_violations

        from tortoise.tool_registry import _ro
        src = (
            "def _get_org_sdk(): ...\n"
            "def _safe(fn, *a, **k): ...\n"
            "def tortoise_probe_readthrough():\n"
            "    return _safe(_get_org_sdk().compute_confidence)\n"
        )
        entry = _probe("tortoise_probe_readthrough", "compute_confidence",
                       annotations=_ro(), http_policy=True)
        assert write_classification_violations([entry], src) == []

    def test_T1_nested_stub_guard_and_wrapper_dispatch_fail(self):
        """A nested stub named like the HTTP check is not a guard; a wrapped
        dynamic lookup (`h = TABLE.get(k) or DEFAULT; h()`) fails closed."""
        from tool_surface_capabilities import (
            binding_resolution_violations,
            handler_self_guards,
            write_classification_violations,
        )

        from tortoise.tool_registry import _ro, _rw
        stub_src = (
            "def _http_excluded_error(): ...\n"
            "def _get_org_sdk(): ...\n"
            "def _safe(fn, *a, **k): ...\n"
            "def tortoise_excl_writer():\n"
            "    def _transport_http_stub():\n"
            "        return False\n"
            "    if _transport_http_stub():\n"
            "        return _http_excluded_error()\n"
            "    return _safe(_get_org_sdk().create_point, 'x')\n"
        )
        assert not handler_self_guards("tortoise_excl_writer", stub_src)
        excl = _probe("tortoise_excl_writer", "create_point", annotations=_rw(),
                      http_policy=False)
        assert write_classification_violations([excl], stub_src)

        wrapper_src = (
            "def _get_org_sdk(): ...\n"
            "def _safe(fn, *a, **k): ...\n"
            "def tortoise_merged_read():\n"
            "    h = _TABLE.get('cp') or _get_org_sdk().update_point\n"
            "    return h()\n"
        )
        merged = _probe("tortoise_merged_read", "query", annotations=_ro(),
                        http_policy=True)
        assert binding_resolution_violations([merged], wrapper_src)

    def test_synthetic_source_drives_filesystem_and_operator_only(self):
        """Every derivation reflects an injected source, not just real data."""
        from tool_surface_capabilities import (
            sdk_filesystem_methods,
            sdk_operator_only_mutators,
        )
        fs_src = (
            "class TortoiseSDK:\n"
            "    def walker(self, directory):\n"
            "        from pathlib import Path\n"
            "        return list(Path(directory).rglob('*.md'))\n"
        )
        assert "walker" in sdk_filesystem_methods(fs_src)
        op_src = (
            "class TortoiseSDK:\n"
            "    def tenant_write(self):\n"
            "        self._get_registry().query('MERGE (t:Graph {id:$i})')\n"
            "    def control_write(self):\n"
            "        self._get_registry().query('MERGE (t:Team {id:$i})')\n"
        )
        op = sdk_operator_only_mutators(op_src)
        assert "control_write" in op and "tenant_write" not in op

    def test_module_level_cypher_constant_is_a_mutator(self):
        """Cypher held in a module-level constant is still a graph mutation."""
        from tool_surface_capabilities import sdk_graph_mutators
        src = (
            "CREATE_X = 'CREATE (n:Point {id: $id})'\n"
            "class TortoiseSDK:\n"
            "    def create_via_const(self):\n"
            "        return self._graph_query(CREATE_X)\n"
        )
        assert "create_via_const" in sdk_graph_mutators(src)


class TestToolIdentity:
    """#4170: the write permission lives on the entry, on a stable `id`.

    The parallel name list `WRITE_TOOL_NAMES` is now DERIVED from the entries,
    so a rename or a merge cannot leave the permission behind. These are
    tripwires, not an analyser.
    """

    def test_ids_are_non_empty_and_unique(self):
        """Every entry carries an immutable, unique id (the rename-free key)."""
        import dataclasses

        from tortoise.tool_registry import TOOL_REGISTRY
        ids = [t.id for t in TOOL_REGISTRY]
        assert all(ids), f"entry with empty id: {[t.name for t in TOOL_REGISTRY if not t.id]}"
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        assert not dupes, f"duplicate ids: {dupes}"
        # the permission and the id are FIELDS on the entry: a rename keeps both
        writer = next(t for t in TOOL_REGISTRY if t.writes)
        renamed = dataclasses.replace(writer, name="tortoise_renamed_probe")
        assert renamed.writes is True and renamed.id == writer.id

    def test_write_names_are_derived_from_the_entries(self):
        """WRITE_TOOL_NAMES is the derivation, not a parallel list.

        Derived over the SERVED set (#3883), exactly like `get_write_tool_names`:
        a retired name still answers, so a write under a retired name must still
        count as a write. No retired entry is a writer today, so stating it over
        the served set changes no number — it stops the census from silently
        shrinking when one is retired."""
        from tortoise.mcp_server import WRITE_TOOL_NAMES
        from tortoise.tool_registry import RETIRED_TOOL_REGISTRY, TOOL_REGISTRY
        derived = frozenset(
            t.name for t in (*TOOL_REGISTRY, *RETIRED_TOOL_REGISTRY) if t.writes
        )
        assert derived == WRITE_TOOL_NAMES
        # 42 is today's declared write census (#4170) — a change here is a
        # permission change and needs the owner's eye, not a test edit.
        assert len(WRITE_TOOL_NAMES) == 42, f"write census moved: {len(WRITE_TOOL_NAMES)}"

    def test_quota_gated_tools_are_declared_writers(self):
        """Every _quota_gated wrap site's bound tool declares writes=True."""
        from tool_surface_capabilities import (  # noqa: I001
            quota_gated_wrap_sites, registry_entries_by_method,
        )
        sites = quota_gated_wrap_sites()
        assert sites.sites, "wrap-site scan is vacuous"
        by_method = registry_entries_by_method()
        for method, lineno, _weight in sites.sites:
            for entry in by_method.get(method, []):
                assert entry.writes, (
                    f"{entry.name} bound to _quota_gated({method})@{lineno} "
                    f"but declared writes=False")

    def test_gate_reads_the_entry_writes_flag(self):
        """The gate's decision IS the entry's `writes` flag: a writer needs
        graphs:write, a read tool is satisfied by graphs:read."""
        from fastmcp.exceptions import AuthorizationError

        from tortoise.mcp_auth import _current_legacy_full_access, _current_scopes
        from tortoise.mcp_server import _enforce_mcp_tool_scope

        scopes_tok = _current_scopes.set(["graphs:read"])
        legacy_tok = _current_legacy_full_access.set(False)
        try:
            with pytest.raises(AuthorizationError):
                _enforce_mcp_tool_scope("tortoise_create_point")  # writes=True
            _enforce_mcp_tool_scope("tortoise_query")             # writes=False
        finally:
            _current_scopes.reset(scopes_tok)
            _current_legacy_full_access.reset(legacy_tok)

    def test_unknown_tool_is_denied_not_served_as_read(self):
        """An unresolvable name is fail-closed — never served as a read."""
        from fastmcp.exceptions import AuthorizationError

        from tortoise.mcp_auth import _current_legacy_full_access, _current_scopes
        from tortoise.mcp_server import _enforce_mcp_tool_scope

        scopes_tok = _current_scopes.set(["graphs:read", "graphs:write"])
        legacy_tok = _current_legacy_full_access.set(False)
        try:
            with pytest.raises(AuthorizationError):
                _enforce_mcp_tool_scope("tortoise_definitely_not_a_tool")
        finally:
            _current_scopes.reset(scopes_tok)
            _current_legacy_full_access.reset(legacy_tok)
