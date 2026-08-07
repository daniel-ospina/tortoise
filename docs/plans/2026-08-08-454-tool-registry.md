---
title: "Canonical Tool Registry — Implementation Plan (#454)"
type: engineering
domain: platform
doc_status: live
created: 2026-08-08
subjects.team: epistemic-team
---

<!-- research-path: docs/epics/2026-08-03-tortoise-hosted-platform/04-plan.md -->

# Canonical Tool Registry — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Eliminate diverging code paths between MCP server (~56 tools) and hosted REST API (~8 tool-ops) by introducing a canonical `tortoise/tool_registry.py` that both surfaces consume — one definition per SDK operation, zero manual sync.

**Team:** epistemic-team
**Role:** (not set)

**Architecture:** A `ToolDefinition` dataclass per SDK operation carries name, description, `ToolAnnotations`, `http_policy` flag, `sdk_method` reference, optional `handler_override`, and optional `RestSpec`. Two adapters consume the registry: `FastMCPAdapter` emits `mcp.add_tool(FunctionTool.from_function(...))` with annotations; `FastAPIRouterAdapter` emits `APIRouter` with route registration, Pydantic models, and surface policies (audit, team limits, dream enqueue). `HTTP_ALLOWED` is derived from registry entries with `http_policy=True`. Control-plane endpoints (`/internal/*`, `/health/*`, `/v1/team/keys/*`, `/v1/team`) stay hand-written.

### Pattern Research

**Canonical library:** FastMCP 3.4.6 `FunctionTool.from_function(annotations=ToolAnnotations | None)` — verified in-venv. `mcp.add_tool(tool)` takes a single tool object. The `@mcp.tool()` decorator internally calls `from_function` with extracted annotations — our adapter replicates this call explicitly.

**Competitor-variance patterns observed:**
- FastMCP's `FastMCP.tool()` decorator wraps `FunctionTool.from_function()` — the adapter is just a different call site
- `ToolAnnotations` is a Pydantic model with `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint` — all optional Booleans
- FastAPI `APIRouter` supports `add_api_route()` for programmatic registration with Pydantic models

**Pitfalls:**
- FastMCP's `add_tool` signature: `def add_tool(self, tool: FunctionTool | BaseTool) -> None` — it takes a single tool object, not fn + annotations separately
- `FunctionTool.from_function` has 16 params — we only need `fn`, `name`, `description`, `annotations`; the rest default
- FastAPI `add_api_route()` requires endpoint functions to be `async def` and use `Depends()` for auth — the adapter must generate async wrappers around sync SDK methods
- Registry must NOT import mcp_server or hosted_api (circular import hazard) — adapters live in the consumers; registry is import-only from stdlib + mcp.types + sdk method references

### Integration Surface Map

| Surface | Layer | Test | Bug Pattern Flags |
|---------|-------|------|-------------------|
| `tool_registry.py` — registry data integrity | Unit | `tests/test_tool_registry.py` | Duplicate tool names; missing annotations; SDK method references dangling |
| `mcp_server.py` — MCP adapter cutover | Integration | `tests/test_mcp_server.py` | tools/list parity; transform accumulation; test-swap pattern broken |
| `mcp_server.py` — HTTP tool filter (derived HTTP_ALLOWED) | Integration | `tests/test_mcp_http.py` | Default-deny regression; excluded tools leaking through HTTP |
| `hosted_api.py` — REST adapter | Integration | `tests/test_hosted_api.py` | Route registration mismatch; audit events missing; Pydantic model drift |
| SDK method references | Unit | `tests/test_tool_registry.py` | Method signature mismatch; raw-Cypher ops missing SDK handle |
| FastMCP `add_tool` API | Spike | Gate 1 pre-step | `from_function(annotations=...)` accepts ToolAnnotations model |

### Journey Test Map

**Journey 1: New SDK method lands in both surfaces**
1. Dev adds `sdk.new_method()` → **Acceptance:** Tests pass for SDK method alone
2. Dev adds one registry entry in `tool_registry.py` → **Acceptance:** `derived_HTTP_ALLOWED` auto-updates; no manual sync
3. MCP surface auto-registers `tortoise_new_method` → **Acceptance:** `tools/list` shows new tool
4. REST surface auto-registers `POST /v1/new-method` → **Acceptance:** Endpoint is live with Pydantic model

**Journey 2: Excluded tool stays excluded**
1. `tortoise_team_create` has `http_policy=False` → **Acceptance:** NOT in derived HTTP_ALLOWED; NOT in REST routes
2. `tortoise_backfill_v25` has `http_policy=False` → **Acceptance:** NOT in derived HTTP_ALLOWED

**Failure Modes:**
- Registry entry has wrong `sdk_method` reference → **Expected:** Gate 1 assertion test catches it (tool name → method lookup mismatch)
- Duplicate tool name → **Expected:** Registry init raises ValueError
- Adapter fails on FastMCP API change → **Expected:** Gate 1 spike validates API contract before transcription

### Verification Plan

| Domain | Depth | Tool | Rationale |
|--------|-------|------|-----------|
| Code — unit | Full | pytest | Registry dataclass + equivalence assertion |
| Code — integration | Full | pytest | MCP adapter tools/list parity; REST route registration |
| Code — regression | Full | pytest (existing suite) | All existing tests green at each gate |
| UX | Skip | — | No UI changes |
| Config | Skip | — | No config changes |
| Research | Skip | — | FastMCP API already verified in-venv |

---

## Task List

### Task 0: FastMCP add_tool SPIKE + test fixture setup

**Intent:** Validate that `FunctionTool.from_function(annotations=ToolAnnotations(...))` produces a tool with annotations intact, before transcribing 56 tools. De-risk the highest-risk unknown.
**Acceptance:** A single spike test proves `mcp.add_tool(FunctionTool.from_function(fn=..., annotations=...))` registers a tool whose `tools/list` response includes annotations. Test file exists at `tests/test_tool_registry.py` with a `TestToolDefinition` class.
**Files:**
- Create: `tests/test_tool_registry.py`
- Modify: (none — spike only; won't be committed as behavior)

**Step 1:** Create `tests/test_tool_registry.py` with a spike test

```python
"""Test tool registry: Gate 1 equivalence + adapter cutover tests."""
import pytest
from fastmcp import FastMCP
from fastmcp.tools import FunctionTool
from mcp.types import ToolAnnotations


class TestFastMCPAddToolSpike:
    """Gate 0: Validate FastMCP 3.4.6 add_tool(from_function(annotations=...))."""

    def test_from_function_with_annotations(self):
        """FunctionTool.from_function preserves annotations in tools/list."""
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
        """add_tool() makes the tool appear in tools/list."""
        mcp = FastMCP("test_spike")
        mcp.add_tool(FunctionTool.from_function(
            lambda x: x,
            name="spike_echo",
            description="Echo tool for spike.",
            annotations=ToolAnnotations(readOnlyHint=True),
        ))
        # Access via FastMCP's internal tool manager
        tool_names = [t.name for t in mcp._tool_manager._tools.values()]
        assert "spike_echo" in tool_names
```

**Step 2:** Run spike test
```
python -m pytest tests/test_tool_registry.py::TestFastMCPAddToolSpike -v
```
Expected: 2 PASS

**Step 3:** Commit spike (this gates whether we can use `from_function` directly)
```
git add tests/test_tool_registry.py
git commit -m "spike: validate FastMCP add_tool(from_function(annotations=...)) for #454"
```

---

### Task 1: ToolDefinition dataclass + registry module

**Intent:** Create the canonical `tortoise/tool_registry.py` with `ToolDefinition` dataclass, `RestSpec` dataclass, and the `TOOL_REGISTRY` list populated with 56 entries transcribed from current `@mcp.tool()` decorators. No behavior change — this is a data module only.
**Acceptance:** `TOOL_REGISTRY` contains 56 entries. Each entry has `name`, `description`, `annotations`, `http_policy`, `sdk_method`. `derived_HTTP_ALLOWED == current HTTP_ALLOWED` (from `mcp_auth.py` on the #236 branch). Import doesn't trigger circular imports.
**Files:**
- Create: `tortoise/tool_registry.py`
- Modify: `tests/test_tool_registry.py`

**Step 1:** Write failing equivalence test in `tests/test_tool_registry.py`

```python
class TestRegistryEquivalence:
    """Gate 1: Derived HTTP_ALLOWED == literal HTTP_ALLOWED."""

    def test_derived_http_allowed_equals_literal(self):
        """Every tool with http_policy=True must be in HTTP_ALLOWED, and vice versa."""
        from tortoise.tool_registry import TOOL_REGISTRY
        derived = frozenset(
            t.name for t in TOOL_REGISTRY if t.http_policy
        )
        # Literal set from mcp_auth.py (the gapless source of truth until cutover)
        from tortoise.mcp_auth import HTTP_ALLOWED
        assert derived == HTTP_ALLOWED, (
            f"Derived HTTP_ALLOWED mismatch:\n"
            f"  In derived but not literal: {derived - HTTP_ALLOWED}\n"
            f"  In literal but not derived: {HTTP_ALLOWED - derived}"
        )

    def test_registry_count(self):
        """56 tools — same count as @mcp.tool() decorators."""
        from tortoise.tool_registry import TOOL_REGISTRY
        assert len(TOOL_REGISTRY) == 56, f"Expected 56, got {len(TOOL_REGISTRY)}"

    def test_no_duplicate_names(self):
        """No two registry entries share the same name."""
        from tortoise.tool_registry import TOOL_REGISTRY
        names = [t.name for t in TOOL_REGISTRY]
        assert len(names) == len(set(names)), f"Duplicates: {[n for n in names if names.count(n) > 1]}"

    def test_http_policy_exclusions(self):
        """Known exclusions are http_policy=False."""
        from tortoise.tool_registry import TOOL_REGISTRY
        by_name = {t.name: t for t in TOOL_REGISTRY}
        excluded = {"tortoise_team_create", "tortoise_backfill_v25", "tortoise_ingest_corpus"}
        for name in excluded:
            assert name in by_name, f"Missing tool: {name}"
            assert by_name[name].http_policy is False, f"{name} should be excluded"
```

**Step 2:** Run — expected FAIL (module doesn't exist)
```
python -m pytest tests/test_tool_registry.py::TestRegistryEquivalence -v
```

**Step 3:** Create `tortoise/tool_registry.py` with all 56 entries.
Structure:

```python
"""Canonical tool registry — single source of truth for all Tortoise operations.

One ToolDefinition per SDK method. Both MCP and REST surfaces derive their
registrations from this registry. HTTP_ALLOWED is derived — zero manual sync.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from mcp.types import ToolAnnotations


@dataclass(frozen=True)
class RestSpec:
    """REST route metadata for SDK-backed operations."""
    method: str          # "GET" | "POST" | "DELETE"
    path: str            # "/v1/points" etc.
    request_model: type | None = None  # Pydantic request model
    response_model: type | None = None # Pydantic response model
    # Status code overrides
    status_code: int = 200


@dataclass(frozen=True)
class ToolDefinition:
    """One entry per SDK-exposed operation."""
    name: str                    # e.g. "tortoise_create_point"
    description: str             # Docstring for MCP + OpenAPI
    annotations: ToolAnnotations # readOnlyHint, destructiveHint, idempotentHint
    http_policy: bool            # True = exposed on HTTP surfaces
    sdk_method: str              # Attribute name on TortoiseSDK, e.g. "create_point"
    handler_override: Optional[Callable] = None  # For raw-Cypher REST ops
    rest_spec: Optional[RestSpec] = None         # For SDK-backed REST ops


# ── Registry Entries ────────────────────────────────────────────
# Maintained in the same order as mcp_server.py for diffability.
# New tools: add one entry here → both surfaces pick it up.

TOOL_REGISTRY: list[ToolDefinition] = [
    ToolDefinition(
        name="tortoise_create_point",
        description="Create a Point node (statement, decision, vision, hypothesis, etc.).",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True),
        http_policy=True,
        sdk_method="create_point",
        rest_spec=RestSpec(method="POST", path="/v1/points",
                          request_model=CreatePointRequest,
                          response_model=PointResponse),
    ),
    # ... 55 more entries ...
]

# ── Derived sets ─────────────────────────────────────────────────
def get_http_allowed() -> frozenset[str]:
    """Derive HTTP_ALLOWED from registry — zero manual sync."""
    return frozenset(t.name for t in TOOL_REGISTRY if t.http_policy)
```

**Step 4:** Rerun equivalence test — iterate until PASS
```
python -m pytest tests/test_tool_registry.py::TestRegistryEquivalence -v
```

**Step 5:** Verify import doesn't trigger circular imports
```
python -c "from tortoise.tool_registry import TOOL_REGISTRY; print(len(TOOL_REGISTRY))"
```

**Step 6:** Commit
```
git add tortoise/tool_registry.py tests/test_tool_registry.py
git commit -m "feat: add ToolDefinition dataclass + registry module (Gate 1) for #454"
```

---

### Task 2: FastMCPAdapter — programmatic tool registration

**Intent:** Create the adapter that reads `TOOL_REGISTRY` and emits `mcp.add_tool(FunctionTool.from_function(...))` for each entry. Remove only the `@mcp.tool()` decorator lines from `mcp_server.py` — preserve all tool function bodies as callable handlers. The adapter wraps each function via `FunctionTool.from_function(fn=function_body, name=..., description=..., annotations=...)`.
**Acceptance:** `tools/list` output identical before/after cutover. All existing MCP tests pass (`test_mcp_server.py`, `test_mcp_http.py`). `_http_tool_filter_registered` guard preserved. Test-swap pattern (`mcp_mod.sdk = test_sdk`) intact. Tool function bodies remain in `mcp_server.py` (only decorators removed).
**Files:**
- Create: `tortoise/tool_registry.py` (add `FastMCPAdapter` class)
- Modify: `tortoise/mcp_server.py` (replace `@mcp.tool()` blocks with registry call)

**Step 1:** Write the adapter test

```python
class TestFastMCPAdapter:
    """Gate 2: MCP adapter emits correct tools from registry."""

    def test_adapter_registers_all_tools(self):
        """Every registry entry becomes a registered MCP tool."""
        from tortoise.tool_registry import TOOL_REGISTRY, FastMCPAdapter
        from fastmcp import FastMCP

        mcp = FastMCP("test_adapter")
        adapter = FastMCPAdapter(mcp)
        # Use a minimal SDK mock — all tools get the same sdk instance
        adapter.register_all(TOOL_REGISTRY, sdk_getter=lambda: None)

        registered = {t.name for t in mcp._tool_manager._tools.values()}
        expected = {t.name for t in TOOL_REGISTRY}
        missing = expected - registered
        assert not missing, f"Tools not registered: {missing}"
        extra = registered - expected
        assert not extra, f"Unexpected tools: {extra}"

    def test_adapter_preserves_annotations(self):
        """ToolAnnotations from registry appear on registered tools."""
        from tortoise.tool_registry import TOOL_REGISTRY, FastMCPAdapter
        from fastmcp import FastMCP

        mcp = FastMCP("test_annotations")
        adapter = FastMCPAdapter(mcp)
        adapter.register_all(TOOL_REGISTRY, sdk_getter=lambda: None)

        # Spot-check: create_point is readOnly=False, idempotentHint=True
        tool = mcp._tool_manager._tools["tortoise_create_point"]
        assert tool.annotations.readOnlyHint is False
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.destructiveHint is False
```

**Step 2:** Implement `FastMCPAdapter` in `tortoise/tool_registry.py`

**Step 3:** Cut over `mcp_server.py`:
- Remove only the `@mcp.tool()` decorator lines (56 lines) — **preserve every function body** (they are the handler callables the adapter wraps)
- Add at the bottom (before `main()`):

```python
from tortoise.tool_registry import TOOL_REGISTRY, FastMCPAdapter

_adapter = FastMCPAdapter(mcp)
_adapter.register_all(TOOL_REGISTRY, sdk_getter=_get_sdk)
```

⚠️ **DO NOT delete the tool function bodies.** The adapter calls `FunctionTool.from_function(fn=tortoise_create_point, name="tortoise_create_point", ...)` — each function must remain as a module-level callable.

**Step 4:** Run existing MCP tests — must all PASS
```
python -m pytest tests/test_mcp_server.py tests/test_mcp_http.py -v
```

**Step 5:** Commit
```
git add tortoise/tool_registry.py tortoise/mcp_server.py tests/test_tool_registry.py
git commit -m "feat: FastMCPAdapter cutover — registry-driven tool registration (Gate 2) for #454"
```

---

### Task 3: Derive HTTP_ALLOWED from registry

**Intent:** Replace the literal `HTTP_ALLOWED` frozenset in `mcp_auth.py` with a derived import from `tool_registry.get_http_allowed()`. Preserve the `_HTTPToolFilter` transform and `_http_tool_filter_registered` guard.
**Acceptance:** `test_http_allowed_populated_default_deny` passes with derived set. `HTTP_ALLOWED` value is identical to literal.
**Files:**
- Modify: `tortoise/mcp_auth.py`
- Modify: `tests/test_mcp_http.py` (if needed)

**Step 1:** Replace literal `HTTP_ALLOWED` with derived import

```python
# In mcp_auth.py — replace the literal frozenset:
from tortoise.tool_registry import get_http_allowed as _get_http_allowed
HTTP_ALLOWED: frozenset[str] = _get_http_allowed()
```

**Step 2:** Run equivalence assertion + HTTP tests
```
python -m pytest tests/test_tool_registry.py::TestRegistryEquivalence tests/test_mcp_http.py -v
```

**Step 3:** Commit
```
git add tortoise/mcp_auth.py
git commit -m "feat: derive HTTP_ALLOWED from tool registry (Gate 2 finish) for #454"
```

---

### Task 4: REST endpoint classification + RestSpec entries

**Intent:** Pre-step for Gate 3: classify 8 REST tool-ops as SDK-backed vs raw-Cypher, add `RestSpec` entries to SDK-backed ops, document raw-Cypher ops with plan to extract SDK methods (or exclude from registry). The 4 raw-Cypher ops (`list_points`, `get_point`, `capture_session`, `list_sessions`) need `sdk_method` references extracted or documented as exclusions.
**Acceptance:** Registry has `rest_spec` populated for 4 SDK-backed ops. 4 raw-Cypher ops have `handler_override` set (with existing raw-Cypher handler) OR are documented as excluded. `capture_session` dedup bug is documented (filed as separate issue).
**Files:**
- Modify: `tortoise/tool_registry.py`
- Modify: `tortoise/hosted_api.py` (add handler references for raw-Cypher ops if needed)

**Step 1:** Add `RestSpec` for SDK-backed ops + `handler_override` for raw-Cypher ops

Classification:
| REST endpoint | SDK-backed? | SDK method |
|---|---|---|
| `POST /v1/points` | YES | `create_point` |
| `GET /v1/points` | RAW CYPHER | (needs `list_points` extraction) |
| `GET /v1/points/{id}` | RAW CYPHER | (needs `get_point_by_id` extraction — SDK `get_point` exists but differs) |
| `POST /v1/dream` | YES | `dream` |
| `GET /v1/search` | YES | `tortoise_fts_query` |
| `POST /v1/sessions` | RAW CYPHER | (needs `capture_session` extraction — has content_hash dedup bug) |
| `GET /v1/sessions` | RAW CYPHER | (needs `list_sessions` extraction) |
| `GET /v1/context` | YES | `session_context` |

**Step 2:** Populate `rest_spec` on SDK-backed entries; set `handler_override` to the raw-Cypher function references for the 4 raw ops.

**Step 3:** Commit
```
git add tortoise/tool_registry.py
git commit -m "feat: classify REST endpoints + add RestSpec entries (Gate 3 pre-step) for #454"
```

---

### Task 5: FastAPIRouterAdapter — programmatic REST route registration

**Intent:** Create the adapter that reads `TOOL_REGISTRY` entries with `rest_spec` populated and generates FastAPI route registrations via `APIRouter.add_api_route()`. Wire through `hosted_api.py`'s `create_http_app` or equivalent. Surface policies (audit logging, team limits, dream enqueue) stay in the route handlers — adapter only registers routes.
**Acceptance:** All 8 REST tool-ops are registered via adapter (4 SDK-backed + 4 raw-Cypher with handler_override). `test_hosted_api.py` tests pass. Existing REST routes match pre-cutover paths, methods, and response shapes.
**Files:**
- Create: `tortoise/tool_registry.py` (add `FastAPIRouterAdapter` class)
- Modify: `tortoise/hosted_api.py`

**Step 1:** Write adapter test

```python
class TestFastAPIRouterAdapter:
    """Gate 3: REST adapter generates correct routes from registry."""

    def test_adapter_registers_all_rest_routes(self):
        """Every registry entry with rest_spec becomes a route."""
        from tortoise.tool_registry import TOOL_REGISTRY, FastAPIRouterAdapter
        from fastapi import FastAPI, APIRouter

        app = FastAPI()
        router = APIRouter()
        adapter = FastAPIRouterAdapter(router)
        adapter.register_all(TOOL_REGISTRY, sdk_getter=lambda team_id: None)

        app.include_router(router)
        routes = [(r.methods, r.path) for r in app.routes if hasattr(r, 'methods')]
        # Verify key routes exist
        route_paths = {(list(m)[0] if m else "", p) for m, p in routes}
        assert ("POST", "/v1/points") in route_paths
        assert ("GET", "/v1/points") in route_paths
        assert ("POST", "/v1/dream") in route_paths
        assert ("GET", "/v1/search") in route_paths
```

**Step 2:** Implement `FastAPIRouterAdapter` in `tortoise/tool_registry.py`

**Step 3:** Wire into `hosted_api.py` — replace manual route decorators with adapter call.
For raw-Cypher ops, preserve existing handler functions as `handler_override` references.

**Step 4:** Run hosted API tests
```
python -m pytest tests/test_hosted_api.py -v
```

**Step 5:** CLI smoke test
```
python -m tortoise hosted_api &
curl http://localhost:8000/health
curl http://localhost:8000/v1/search?q=test
kill %1
```

**Step 6:** Commit
```
git add tortoise/tool_registry.py tortoise/hosted_api.py tests/test_tool_registry.py
git commit -m "feat: FastAPIRouterAdapter — registry-driven REST routes (Gate 3) for #454"
```

---

### Task 6: Cleanup — remove dead code + final regression

**Intent:** Remove any remaining manual `@mcp.tool()` decorator-created tool bodies (if any were left as comments), verify zero divergence, update the dangling 236-tool-scope-table reference (see #491), and run full regression.
**Acceptance:** `grep -r '@mcp.tool()' tortoise/` returns zero results (or only the registry registration call). `grep -r '@app.(get|post|delete)' tortoise/hosted_api.py` returns only control-plane endpoints. All tests pass. Dangling 236-tool-scope-table.md reference updated to point at the real source: `tortoise/tool_registry.py` (`get_http_allowed()`) + `tortoise/mcp_auth.py` (`HTTP_ALLOWED`, registry-derived per #454).
**Files:**
- Modify: `tortoise/mcp_server.py`
- Modify: `tortoise/hosted_api.py`
- Modify: `tortoise/mcp_auth.py` (optional — update docstring reference)

**Step 1:** Verify zero `@mcp.tool()` decorators remain
```
grep -c '@mcp.tool()' tortoise/mcp_server.py
```
Expected: 0 (or 0 in tool-definition context)

**Step 2:** Verify REST routes are adapter-driven
```
grep '@app\.\(get\|post\|delete\)' tortoise/hosted_api.py
```
Expected: only control-plane routes (/internal/*, /health/*, /v1/team/*, /v1/team/keys/*)

**Step 3:** Full test suite
```
python -m pytest tests/ -v
```

**Step 4:** Commit
```
git add -A
git commit -m "feat: cleanup — remove dead code, final regression for #454"
```

---

## Next: Plan Review Gate

After saving, invoke `plan-review` to validate this plan before execution.

### Task Count: 7 tasks → Subagent-Driven (this session)

## Branch Prerequisites

Before starting implementation:
1. **Ensure `mcp_auth.py` is available:** The equivalence test in Task 1 imports `HTTP_ALLOWED` from `tortoise/mcp_auth.py`. This file was created in #236 (MCP Streamable HTTP) and lives on sibling branches. If missing from this branch, rebase onto or merge from the latest `main` that includes #236.
2. **Tool count note:** The registry transcription covers 56 tools present on this branch. The `mcp_auth.py` from post-#485 branches includes 2 additional tools (`tortoise_list_tags`, `tortoise_query_points_by_tag`) — the equivalence test will need those registry entries added if `mcp_auth.py` is taken from a post-#485 state. Verify tool count by running `grep -c '@mcp.tool()' tortoise/mcp_server.py` before starting Task 1.
3. **Verify FastMCP 3.4.6 is installed:** `python -c "import fastmcp; assert fastmcp.__version__ == '3.4.6'"`

<!-- plan-review: cycles=1, status=clean, version=2.2.0, mode=inline-review -->
<!-- review-summary: 3 issues found (2 P1, 1 P2), all fixed inline; no structural blockers -->
