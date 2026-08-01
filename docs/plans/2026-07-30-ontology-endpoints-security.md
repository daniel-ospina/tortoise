---
title: "Backend: 7 Ontology Endpoints + Cypher Security Hardening — Implementation Plan"
type: plan
domain: capability
doc_status: draft
created: 2026-07-30
subjects.team: organisation-design-team
---

<!-- research-path: none — zero new deps -->

# Backend: 7 Ontology Endpoints + Cypher Security Hardening — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Port 7 ontology API endpoints from reference implementation and fix systemic Cypher injection, CORS, host binding, health endpoint, and Pydantic validation across all graph-viz endpoints.

**Team:** organisation-design-team

**Architecture:** Single-file FastAPI backend (`apps/graph-viz/server/main.py`) talking to FalkorDB. Port the 7 ontology endpoints from the reference implementation at `/Users/home/eldato/apps/graph-viz/server/main.py` (819 lines, already parameterized, already hardened). Convert ALL existing `%s`-formatted Cypher to `$param` parameterized queries. Existing graph endpoints (`/api/graph`, `/api/search`, etc.) need structural rewrites where `IN [...]` list patterns can't be mechanically parameterized.

### Pattern Research

Skipped — plan touches zero third-party dependencies. FalkorDB and Pydantic are already in `requirements.txt`.

### Integration Surface Map

| Touch Point | Type | Test Layer | Notes |
|-------------|------|------------|-------|
| FalkorDB `ro_query` / `query` | DB | Integration (live DB) | All queries use `$param` dicts via FalkorDB client |
| FastAPI endpoints | API | Integration | 7 new + hardening of existing |
| CORS middleware | Config | Manual | Restrict from `["*"]` to `["http://localhost:5173"]` |
| Uvicorn host binding | Config | Manual | Change `0.0.0.0` → `127.0.0.1` |
| Health endpoint | API | Smoke | Remove topology leak, add `ontology_status` |

### Verification Plan

- **Unit:** None (single-file backend, no extractable functions)
- **Integration:** `pytest` with live FalkorDB — test each new endpoint at least once
- **Smoke:** `curl` health endpoint, tree endpoint, object-arguments
- **Manual:** Verify CORS headers, host binding
- **Skipped:** E2E (frontend #7593 not ready), pgTAP (no SQL functions)

---

### Task 1: Security Hardening — Harden All Existing Endpoints

**Intent:** Fix systemic Cypher injection, restrict CORS, bind to 127.0.0.1, remove topology leak from health, add Pydantic validation. Fixes VULN-001 through VULN-011.

**Acceptance:** All Cypher queries use `$param` dicts. CORS restricted to `["http://localhost:5173"]`. Server binds to `127.0.0.1`. Health endpoint returns `ontology_status` without topology. Pydantic models have `max_length` constraints.

**Files:**
- Modify: `apps/graph-viz/server/main.py` (entire file)
- Modify: `apps/graph-viz/server/requirements.txt` (remove playwright if present)

**Step 1: Rewrite app setup — CORS + Pydantic + config**
- Change `allow_origins=["*"]` → `allow_origins=["http://localhost:5173"]`
- Add `from pydantic import field_validator` and `from typing import Optional`
- Extract `VALID_OBJECT_KINDS`, `CONTEXT_DEFAULT`, `NODE_CAP_ALL_CONTEXT`, `ARGUMENT_EDGE_CAP` constants
- Change `uvicorn.run(app, host="0.0.0.0", port=8000)` → `host="127.0.0.1"`

**Step 2: Harden health endpoint**
- Remove `host`, `port`, `available_graphs` from response (topology leak)
- Add `ontology_status` field (tries `MATCH (n:Point:Object ...) RETURN count(n)`)
- Keep only: `status`, `ontology_status`, `falkordb_connected`

**Step 3: Convert all existing Cypher queries to parameterized**
- `_query_graph()`: Replace `f"... WHERE n.id IN [{ids_str}]"` with UNWIND pattern using `$ids` param
- `get_neighborhood()`: Same UNWIND conversion
- `/api/search`: Replace `%s` with `$q` param
- `/api/points` POST: Replace `%s` with `$param` dicts
- `/api/points` DELETE: Replace `%s` with `$param` dict
- `/api/edges` POST: Replace `%s` with `$param` dict
- `/api/edges` DELETE: Replace `%s` with `$param` dict
- `_build_cache()`: Already safe (no user input), keep as-is

**Step 4: Add Pydantic field validation**
- Add `field_validator` for `content` (max_length=500) on `PointCreate`
- Add `field_validator` for `type` on `EdgeCreate` (max_length=50)

**Step 5: Check requirements.txt**
- Remove `playwright` if present
- Verify `pydantic>=2.0` and `falkordb>=1.0` are listed

---

### Task 2: Port 7 Ontology Endpoints

**Intent:** Add ontology-tree, object CRUD, descendants, and object-arguments endpoints. Port from reference implementation, adapting to premise-labs FalkorDB defaults (port 16379, graph "tortoise").

**Acceptance:** 7 endpoints return documented shapes. 409 Conflict on version mismatch and cascade delete guard. Edge cap at 50 for arguments.

**Files:**
- Modify: `apps/graph-viz/server/main.py` (append endpoints)

**Step 1: Add helper functions**
- `_get_graph()` — return FalkorDB connection (adapt to existing DB_HOST/PORT/PASSWORD from startup)
- `_new_id()` — UUID-based ID generation
- `_now_iso()` — UTC ISO timestamp
- `_node_to_dict()` — convert FalkorDB Node to dict
- `_result_to_dicts()` — convert result set to list of dicts

**Step 2: Add Pydantic models**
- `CreateObjectRequest(name, objectKind, context, parentId, content)` with field_validator for objectKind enum
- `UpdateObjectRequest(name, content)` — optional fields

**Step 3: Add GET /api/ontology-tree?context=&root_only=**
- Fetch all `:Point:Object` nodes (cap at 200 for context="all")
- Fetch `hasPart` edges
- Assemble tree server-side (cycle guard, build_subtree recursion)
- Return `{tree, total_nodes, context, filtered_from}`

**Step 4: Add GET /api/ontology-object/{id}/descendants**
- Traverse `hasPart*` from target node
- Return `{node, descendants[], total_descendants}`

**Step 5: Add POST /api/ontology-object (status_code=201)**
- Create dual-label `:Point:Object` node
- Optional `parentId` → creates `hasPart` edge
- Return created node with version

**Step 6: Add PUT /api/ontology-object/{id} (If-Match concurrency)**
- Require `If-Match` header with version
- 409 on version mismatch
- Update name/content, bump version

**Step 7: Add DELETE /api/ontology-object/{id}?force= (If-Match + cascade guard)**
- Require `If-Match` header
- 409 if has children and force≠true
- Cascade delete with `hasPart*0..` when force=true

**Step 8: Add GET /api/object-arguments?id=**
- Fetch IMPL edges (supports) and NAND edges (contradicts)
- Include mitigations array per edge
- Cap at 50 total

**Step 9: Add ontology_status to /api/health**
- Verify `:Point:Object` query works
- Set `ontology_status: "ok"` on success

---

### Task 3: Verification

**Intent:** Verify all 7 new endpoints and all hardened existing endpoints work correctly.

**Acceptance:** Health endpoint returns `ontology_status: "ok"`. Tree endpoint returns valid JSON tree. Object CRUD operations work. Arguments endpoint returns capped supports/contradicts.

**Files:**
- Test: `apps/graph-viz/server/main.py` (start server + curl tests)

**Step 1: Start server**
```bash
cd apps/graph-viz/server && python main.py &
sleep 2
```

**Step 2: Smoke test health**
```bash
curl -s http://127.0.0.1:8000/api/health | python -m json.tool
```
Expected: `{"status": "ok", "ontology_status": "...", "falkordb_connected": true}`

**Step 3: Smoke test tree**
```bash
curl -s "http://127.0.0.1:8000/api/ontology-tree?context=product-strategy" | python -c "import sys,json; d=json.load(sys.stdin); print(f'tree nodes: {d.get(\"total_nodes\",0)}')"
```

**Step 4: Smoke test object-arguments**
```bash
# First get a node ID from tree, then:
curl -s "http://127.0.0.1:8000/api/object-arguments?id=<some-id>" | python -c "import sys,json; d=json.load(sys.stdin); print(f'supports: {len(d.get(\"supports\",[]))}, contradicts: {len(d.get(\"contradicts\",[]))}')"
```

**Step 5: Kill server**
```bash
kill %1
```

---

### Task 4: Code Review + Commit

**Intent:** Run code-review skill, fix issues, then commit via commit-workflow.

**Acceptance:** Code review passes with NO ISSUES FOUND. Commit follows commit-workflow skill (pre-flight checks, PR, review gate, auto-merge).

**Step 1: Run code-review**
- Dispatch parallel reviewers via `code-review` skill
- Fix issues in review loop
- Exit when clean

**Step 2: Commit via commit-workflow**
- Read `commit-workflow/SKILL.md` before committing
- Stage `apps/graph-viz/server/main.py` and `requirements.txt`
- Create PR, pass review gate, auto-merge
