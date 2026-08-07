# Demo Graph + MCP Tools — Implementation Plan

> **Issue:** #500 (child of epic #235) | **Complexity:** STANDARD (Architecture)
> **Dependencies:** #498 (API plan — defines tool names + demo endpoint), #496 (question set — Q4 demo graph content), #495 (AGENT_ONBOARDING.md — prompt contract)
> **Sibling issues:** #499 (GitHub MCP tools — `tortoise_onboarding_github_*`), #501 (team creation + completion)
> **Plan author:** issue-scoping v5.1 double diamond | **Date:** 2026-08-07

## Confirmed Problem Definition

The hosted onboarding prompt (AGENT_ONBOARDING.md, #495) invokes three MCP tools that don't exist yet:

| Tool | Used in | What it does |
|------|---------|-------------|
| `tortoise_onboarding_demo_create` | Q4 — "Create a demo graph?" | Creates a curated epistemic graph so new users see what Tortoise memory looks like |
| `tortoise_onboarding_state` | Q6 — verification step | Records onboarding completion; also read by welcome page for progress display |
| `tortoise_onboarding_session_recording` | Q3 — "Record sessions?" | Toggles whether agent sessions are auto-captured |

These three tools are the **#500 scope**. GitHub tools (`tortoise_onboarding_github_connect`, `_status`, `_index`) belong to #499. Team creation (`tortoise_onboarding_create_team`) belongs to #501.

**Root cause:** The existing `/internal/demo` endpoint is gated behind `FASTAPI_INTERNAL_KEY` — only operators can create demo graphs. Onboarding is self-service: the user needs a public `POST /v1/demo` behind Bearer `tt_` auth. No onboarding state tracking or session-recording toggle exists at all.

**What the prompt contract demands (from AGENT_ONBOARDING.md):**

```
Q4 (demo):
  tortoise_onboarding_demo_create()
  → "✅ Demo graph created — [N] points, [M] operators."

Q3 (session recording):
  tortoise_onboarding_session_recording(enabled=true)
  → "✅ Session recording enabled."

Q6 (completion):
  tortoise_onboarding_state()
  → records completion
```

## Demo Graph Design

### Content: What gets created?

Mine the existing `/internal/demo` (hosted_api.py:784–956) — it already has a production-tested demo across all 4 ontology layers. The public variant retains the same structure but adds **Operators** (SUPPORTS, CONTRADICTS) to make it a proper epistemic graph.

**12 Points + 1 sentinel across 4 layers:**

| Layer | Points | Content |
|-------|--------|---------|
| **Semantic** (3) | `sem_welcome`, `sem_fact_tortoise`, `sem_fact_layers` | Welcome message + Tortoise overview + ontology explanation |
| **Episodic** (3 + 1 Session) | `epi_turn1–3` + `session_demo_*` | 3-turn conversation transcript (user + assistant) linked to a Session node |
| **Epistemic** (3) | `epis_claim1–3` | Hypothesis ("graph-native > vector-only"), evidence ("40% fewer mistakes"), decision ("use FalkorDB") — each with confidence scores |
| **Procedural** (3) | `proc_wf1–3` | Workflow rules: context-injection, decision-capture, review-gate |
| **Sentinel** (1) | `_demo_sentinel` | Written last for idempotency — skip if present |

**3 Operators (NEW — added by #500):**

| Operator | From | To | Type | Meaning |
|----------|------|----|------|---------|
| `op_support_1` | `epis_claim2` (evidence) | `epis_claim1` (hypothesis) | SUPPORTS | Adoption data supports graph-native architecture claim |
| `op_support_2` | `sem_fact_layers` (fact) | `epis_claim1` (hypothesis) | SUPPORTS | Ontology design supports the hypothesis |
| `op_contradicts_1` | `epis_claim3` (decision) | `epis_claim1` (hypothesis) | CONTRADICTS | Choosing FalkorDB (specific) contradicts "graph-native" (general) — healthy tension |

**Why this content over the #496 Q4 5-point scenario (FalkorDB/Fly.io)?**

The #496 question set defines a simplified 5-point scenario for Phase 1 (where the agent calls `tortoise_create_point` ×5 directly). In Phase 2, with a dedicated endpoint, we should use the richer /internal/demo content because:
1. It already exists and is production-tested
2. It demonstrates all 4 ontology layers (the core Tortoise value prop)
3. It includes cross-layer links (SUPPORTS, INFORMED_BY) — showing how layers connect
4. Operators (added by #500) make it a proper epistemic graph

### Idempotency: Sentinel pattern

Same pattern as `/internal/demo`: write a `_demo_sentinel` Point last. If it exists on re-run → return `{"status": "already_seeded"}`. This is tenant-isolated — each team gets their own demo graph in their namespace graph.

### Tenant isolation

The public endpoint uses `Depends(get_current_team)` → team_id → `_make_sdk(namespace=team_id)`. The SDK's `select_graph` ensures all writes go to the team's isolated graph. Same pattern as every other `/v1/*` endpoint.

### Performance target

< 5 seconds for full demo creation (12 Points + 3 Operators + tags + cross-layer links). The existing `/internal/demo` completes in ~2s on a cold FalkorDB instance.

## MCP Tools

### Tool 1: `tortoise_onboarding_demo_create`

| Field | Value |
|-------|-------|
| **MCP name** | `tortoise_onboarding_demo_create` |
| **Endpoint** | Wraps `POST /v1/demo` |
| **Auth** | Bearer `tt_` (via `TeamResolutionMiddleware`) |
| **Input** | None (team inferred from auth) |
| **Output** | `{status, team_id, session_id, points: 12, layers: {semantic: 3, episodic: 3, epistemic: 3, procedural: 3}, operators_created: 3}` |
| **Idempotent** | ✅ Sentinel-based — re-running returns `{status: "already_seeded"}` |
| **Side effect** | Auto-sets `onboarding_state.demo_created: true` |
| **Annotations** | `readOnlyHint=false, destructiveHint=false, idempotentHint=true` |

### Tool 2: `tortoise_onboarding_state`

| Field | Value |
|-------|-------|
| **MCP name** | `tortoise_onboarding_state` |
| **Endpoint** | Wraps `GET /v1/onboarding/state` and `PATCH /v1/onboarding/state` |
| **Auth** | Bearer `tt_` |
| **Input** | `action: "get" \| "patch"`, `updates: dict \| None` (for patch) |
| **Output (get)** | `{onboarding: {github_connected, github_org, session_recording, demo_created, team_created, completed_at, ...}}` |
| **Output (patch)** | `{onboarding: {...}}` (merged state) |
| **Idempotent** | ✅ GET is read-only; PATCH merges (re-applying same fields is no-op) |
| **Annotations** | `readOnlyHint=false, destructiveHint=false, idempotentHint=true` |

### Tool 3: `tortoise_onboarding_session_recording`

| Field | Value |
|-------|-------|
| **MCP name** | `tortoise_onboarding_session_recording` |
| **Endpoint** | Wraps `POST /v1/onboarding/session-recording` |
| **Auth** | Bearer `tt_` |
| **Input** | `enabled: bool` |
| **Output** | `{session_recording: bool}` |
| **Idempotent** | ✅ Setting to same value twice is a no-op |
| **Side effect** | Updates `onboarding_state.session_recording` |
| **Annotations** | `readOnlyHint=false, destructiveHint=false, idempotentHint=true` |

## Tool Registry Integration (#454)

All three tools are registered in `tortoise/tool_registry.py` following the existing pattern:

```python
ToolDefinition(
    name="tortoise_onboarding_demo_create",
    description="Create a curated demo graph showing all 4 ontology layers "
                "(Semantic, Episodic, Epistemic, Procedural) with sample Points "
                "and Operators (SUPPORTS, CONTRADICTS). Idempotent — re-running "
                "returns 'already_seeded'.",
    annotations=_idem(),
    http_policy=True,
    sdk_method="onboarding_demo_create",  # thin wrapper in mcp_server.py
),
ToolDefinition(
    name="tortoise_onboarding_state",
    description="Get or update onboarding progress state. "
                "Use action='get' to read current state, action='patch' to merge updates. "
                "State persists on the Team node in the registry graph.",
    annotations=_idem(),
    http_policy=True,
    sdk_method="onboarding_state",
),
ToolDefinition(
    name="tortoise_onboarding_session_recording",
    description="Toggle automatic session recording. When enabled, agent sessions "
                "are auto-captured via POST /v1/sessions.",
    annotations=_idem(),
    http_policy=True,
    sdk_method="onboarding_session_recording",
),
```

**Why `http_policy=True`?** These tools must be callable via the hosted MCP surface (the agent prompt runs against the hosted endpoint). The `FastMCPAdapter` registers them; the `TeamResolutionMiddleware` handles auth. All three annotate as `_idem()` because re-running with the same inputs is safe.

**Handler pattern** (in `mcp_server.py`): Each tool is a thin wrapper that calls the REST endpoint via `_safe()`:

```python
async def onboarding_demo_create(ctx: Context) -> dict:
    """Create demo graph via POST /v1/demo."""
    return await _safe(lambda sdk: sdk.onboarding_demo_create())
```

The actual logic lives in the REST endpoint (hosted_api.py); the MCP tool is a pass-through. This keeps the MCP layer thin and the REST endpoint reusable by the welcome page or future dashboard.

## Verification

### Pre-merge verification

| Check | How | Passes when |
|-------|-----|-------------|
| Tools visible in `tools/list` | `curl -H "Authorization: Bearer tt_$KEY" $HOSTED/mcp` → list tools | `tortoise_onboarding_demo_create`, `tortoise_onboarding_state`, `tortoise_onboarding_session_recording` are present |
| Demo create idempotent | Call twice, same tenant | First returns `points: 12, operators_created: 3`; second returns `status: "already_seeded"` |
| Demo tenant-isolated | Call from team A, then team B | Team B gets fresh demo (not team A's); team A's demo unchanged |
| State get returns defaults | `GET /v1/onboarding/state` on fresh team | Returns default state (all false/null) |
| State patch merges | PATCH `{demo_created: true}`, then GET | GET shows `demo_created: true`, other fields unchanged |
| Session recording toggle | POST with `enabled: true`, then `enabled: false` | State reflects each toggle |
| Demo < 5s | Measure wall-clock on cold FalkorDB | Under 5 seconds |
| No cross-contamination with #499 tools | Check tool list for github_* tools | Not present (belong to #499) |

### Post-deploy smoke

1. Provision a new team via `/internal/provision`
2. Call `tortoise_onboarding_demo_create` — verify 12 points + 3 operators
3. Call `tortoise_onboarding_state(action="get")` — verify `demo_created: true`
4. Call `tortoise_onboarding_session_recording(enabled=true)` — verify state
5. Re-call demo → `"already_seeded"`
6. Verify demo points appear in `tortoise_summarize_structure()`

## Implementation Tasks

### Task 1: Public Demo Graph Endpoint (`POST /v1/demo`)

**Intent:** Expose the demo graph creation behind Bearer `tt_` auth. Extract shared logic from `/internal/demo` so both endpoints reuse the same graph builder. Add Operators to the demo (SUPPORTS, CONTRADICTS) — the current /internal/demo has cross-layer links but no formal Operators.

**Acceptance:**
- `POST /v1/demo` creates 12 Points + 3 Operators in the authenticated team's namespace
- Idempotent via sentinel: re-running returns `{"status": "already_seeded"}`
- Returns `{status, team_id, session_id, points: 12, layers: {...}, operators_created: 3}`
- Completes in < 5 seconds (cold FalkorDB)
- Auto-sets `onboarding_state.demo_created: true`

**Files:**
- Modify: `tortoise/hosted_api.py` — extract `_create_demo_graph(team_id)` helper, add `POST /v1/demo`, refactor `/internal/demo` to use helper
- Create: `tests/test_demo_graph.py`

**Steps:**
1. Extract demo graph creation from `/internal/demo` (lines 784–956) into `_create_demo_graph(team_id: str) -> dict` helper
2. Add 3 Operators to the helper (SUPPORTS, CONTRADICTS edges between epistemic points)
3. Add `POST /v1/demo` using `Depends(get_current_team)` + helper
4. Refactor `/internal/demo` to call the same helper (reduces duplication)
5. Add `onboarding_state.demo_created = True` side effect
6. Write tests: `test_demo_creates_points_and_operators`, `test_demo_idempotent_sentinel`, `test_demo_requires_auth`, `test_demo_tenant_isolation`, `test_demo_updates_onboarding_state`
7. Run tests → red → green → refactor

### Task 2: MCP Tool — `tortoise_onboarding_demo_create`

**Intent:** Wire the demo creation as an MCP tool so the agent prompt can call it during Q4.

**Acceptance:**
- Tool registered in `mcp_server.py` via `_safe()` pattern
- Calling the tool creates a demo graph and returns counts
- Tool visible in `tools/list`
- Handles auth failures gracefully (returns structured error, doesn't crash)

**Files:**
- Modify: `tortoise/mcp_server.py` — add handler function + registration
- Modify: `tortoise/tool_registry.py` — add `ToolDefinition` entry
- Create: `tests/test_mcp_onboarding_demo.py`

**Steps:**
1. Add `ToolDefinition` to `TOOL_REGISTRY` with `http_policy=True`, `_idem()` annotations
2. Add handler function in `mcp_server.py` (thin wrapper → calls REST endpoint or SDK helper)
3. Register via `FastMCPAdapter` (handler map)
4. Write tests: `test_tool_listed`, `test_tool_creates_demo`, `test_tool_idempotent`, `test_tool_auth_error`
5. Run tests → red → green → refactor

### Task 3: Onboarding State Endpoints (`GET/PATCH /v1/onboarding/state`)

**Intent:** Persist per-team onboarding progress. The agent prompt's Q6 calls `tortoise_onboarding_state()` to record completion; the welcome page polls this for progress display. State lives on the Team node in the FalkorDB registry graph (co-located with the node `get_current_team` already queries).

**Acceptance:**
- `GET /v1/onboarding/state` returns `{onboarding: {github_connected, github_org, session_recording, demo_created, team_created, completed_at}}`
- `PATCH /v1/onboarding/state` merges provided fields into existing state
- Default state created during provisioning (`/internal/provision` and `/v1/register`)
- Missing state auto-initializes on first read (lazy init for existing teams)
- Invalid field names in PATCH return 422

**Files:**
- Modify: `tortoise/hosted_api.py` — add state endpoints + extend `/internal/provision` to set default state
- Modify: `tortoise/mcp_server.py` — add `tortoise_onboarding_state` handler
- Modify: `tortoise/tool_registry.py` — add `ToolDefinition` entry
- Create: `tests/test_onboarding_state.py`

**Steps:**
1. Define Pydantic models: `OnboardingStateResponse`, `OnboardingStatePatchRequest`
2. Extend `/internal/provision` to set `t.onboarding_state = $default_state` on Team node
3. Implement `GET /v1/onboarding/state` — query Team node → return `onboarding_state` (lazy init if missing)
4. Implement `PATCH /v1/onboarding/state` — validate field names → merge → write back
5. Add `ToolDefinition` + handler for `tortoise_onboarding_state` MCP tool
6. Write tests: `test_get_state_default`, `test_get_state_after_patch`, `test_patch_merges_not_replaces`, `test_patch_invalid_field`, `test_state_requires_auth`, `test_state_lazy_init`
7. Run tests → red → green → refactor

### Task 4: Session Recording Toggle (`POST /v1/onboarding/session-recording`)

**Intent:** Toggle whether agent sessions are auto-captured. The agent prompt Q3 calls `tortoise_onboarding_session_recording(enabled=true)`. The toggle gates the existing `/v1/sessions` auto-capture behavior — when disabled, sessions are not persisted; when enabled, they are.

**Acceptance:**
- `POST /v1/onboarding/session-recording` accepts `{enabled: bool}`, returns `{session_recording: bool}`
- Updates `onboarding_state.session_recording` on the Team node
- Idempotent: setting `enabled=true` twice is a no-op
- Authenticated (Bearer `tt_`)

**Files:**
- Modify: `tortoise/hosted_api.py` — add recording endpoint
- Modify: `tortoise/mcp_server.py` — add `tortoise_onboarding_session_recording` handler
- Modify: `tortoise/tool_registry.py` — add `ToolDefinition` entry
- Create: `tests/test_session_recording.py`

**Steps:**
1. Add `POST /v1/onboarding/session-recording` endpoint (validate input → update Team node → return state)
2. Add `ToolDefinition` to registry with `http_policy=True`, `_idem()` annotations
3. Add handler in `mcp_server.py` (thin wrapper)
4. Write tests: `test_enable_recording`, `test_disable_recording`, `test_toggle_idempotent`, `test_toggle_requires_auth`, `test_toggle_updates_onboarding_state`
5. Run tests → red → green → refactor

### Task 5: End-to-End Integration + Verification

**Intent:** Verify all three tools work together as the agent prompt would use them: demo create → session recording → state tracking. Cross-check against the prompt contract (#495).

**Acceptance:**
- Full flow: provision team → create demo → toggle recording → check state → verify state reflects both actions
- All tools visible in MCP `tools/list`
- Demo idempotent across re-runs
- State survives server restart (persisted on Team node in FalkorDB)

**Files:**
- Create: `tests/test_onboarding_integration.py`

**Steps:**
1. Write integration test: `test_full_onboarding_tool_flow`
2. Write integration test: `test_demo_idempotent_across_restarts`
3. Write integration test: `test_cross_team_isolation`
4. Run full test suite → all green

## Cross-Dependency Notes

| #500 owns | #499 owns | #501 owns |
|-----------|-----------|-----------|
| `tortoise_onboarding_demo_create` | `tortoise_onboarding_github_connect` | `tortoise_onboarding_create_team` |
| `tortoise_onboarding_state` | `tortoise_onboarding_github_status` | `POST /v1/onboarding/complete` |
| `tortoise_onboarding_session_recording` | `tortoise_onboarding_github_index` | |
| `POST /v1/demo` | `POST /v1/onboarding/github/*` | |
| `GET/PATCH /v1/onboarding/state` | `POST /v1/index/github` | |
| `POST /v1/onboarding/session-recording` | | |

**Shared dependency:** All three issues (#499, #500, #501) depend on `GET/PATCH /v1/onboarding/state` existing — it's the persistence layer for onboarding progress. #500 builds it, #499 and #501 consume it (GitHub tools auto-set `github_connected`, team creation auto-sets `team_created`).

**No code dependency on #499:** The demo + state + recording tools are independent of GitHub OAuth. They can be built and shipped before #499 is complete.

## Rejected Alternatives

| Option | Why rejected |
|--------|-------------|
| Demo graph as 5-point FalkorDB/Fly.io scenario only | Too shallow — doesn't demonstrate the 4-layer ontology, which is Tortoise's core differentiator. The /internal/demo already has a richer, production-tested version. |
| Store onboarding state in Supabase | hosted_api.py doesn't talk to Supabase today. Team node in registry graph is already queried on every request — adding one property costs nothing. |
| MCP tools call SDK directly (skip REST) | The welcome page needs REST endpoints to poll state. REST-as-canonical means MCP tools and web dashboard share the same backend. |
| Separate onboarding_state MCP tool per operation | AGENT_ONBOARDING.md calls `tortoise_onboarding_state()` as a single tool. Splitting into `_get` and `_patch` would require a prompt update (#502). Single tool with `action` parameter matches the contract. |
